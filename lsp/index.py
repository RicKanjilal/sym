"""
Analysis of one .sym document: tokens, definitions, scopes, references.

Token-driven on purpose: the index is built from the lexer's token stream, not
the AST, so outline, completion, hover and go-to-definition keep working while
the file is half-typed and does not parse. The AST is used on top, when it
exists, for the things that need it (purity, type checking, emit).
"""

import builtins as _pybuiltins
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from compiler.lexer import Lexer, TT, NORMAL_KEYWORDS, COMPACT_KEYWORDS  # noqa: E402
from compiler.preprocess import embedded_regions, mask_embedded, preprocess  # noqa: E402
from compiler.modes import detect_mode, SPELLING, TYPE_GLYPHS, GLYPH_TYPES  # noqa: E402

BRIDGE_LANGS = ("java", "js", "php", "ruby", "perl", "c", "r", "sym", "python")
STRUCTURAL = (TT.NEWLINE, TT.INDENT, TT.DEDENT, TT.EOF)
KEYWORD_TYPES = set(SPELLING) | {TT.SORT, TT.OPTIMIZE, TT.BENCHMARK, TT.SELF}
OPERATOR_TYPES = {TT.PLUS, TT.MINUS, TT.STAR, TT.SLASH, TT.DSLASH, TT.PERCENT, TT.POWER, TT.EQ,
                  TT.NEQ, TT.LT, TT.GT, TT.LTE, TT.GTE, TT.ASSIGN, TT.PLUS_EQ, TT.MINUS_EQ,
                  TT.STAR_EQ, TT.SLASH_EQ, TT.ARROW, TT.FAT_ARROW, TT.PIPE, TT.DOTDOT}
for _n in ("QUESTION", "AMP", "TILDE"):
    if hasattr(TT, _n):
        OPERATOR_TYPES.add(getattr(TT, _n))


def _runtime_builtins():
    """name -> signature for the Sym runtime + the Python builtins Sym passes through."""
    out = {}
    try:
        import inspect
        from runtime import builtins as rb
        for name in dir(rb):
            if name.startswith("_"):
                continue
            obj = getattr(rb, name)
            if callable(obj) and getattr(obj, "__module__", "") == rb.__name__:
                try:
                    out[name] = name + str(inspect.signature(obj))
                except (TypeError, ValueError):
                    out[name] = name + "(...)"
            elif name.isupper():
                out[name] = f"{name}: {type(obj).__name__}"
    except Exception:
        pass
    for name in ("sqrt", "sin", "cos", "tan", "log", "floor", "ceil", "PI", "E", "INF"):
        out.setdefault(name, name + "(x)" if name.islower() else name)
    out.update({
        "sym_get": "sym_get(key) — read from the shared Symbol space",
        "sym_set": "sym_set(key, value) — write to the shared Symbol space",
        "serve": "serve(config) — HTTP server", "serve_realtime": "serve_realtime(config) — WebSocket server",
    })
    return out


SYM_BUILTINS = _runtime_builtins()
PY_BUILTINS = {n for n in dir(_pybuiltins) if not n.startswith("_")}
KNOWN_TYPES = {"int", "float", "bool", "str", "list", "dict", "set", "tuple", "any", "none", "void",
               "u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "f32", "f64", "bytes"}


@dataclass
class Def:
    name: str
    kind: str                 # function | method | struct | trait | field | variable | constant | parameter | module | loopvar
    line: int                 # 1-based
    col: int                  # 1-based code-point column of the name
    end_col: int
    detail: str = ""
    type: Optional[str] = None
    scope: Optional[int] = None       # index into Index.functions; None = file scope
    container: Optional[str] = None   # struct/trait name for fields and methods
    bridge: Optional[tuple] = None    # (lang, target) for imported foreign modules
    doc: str = ""
    default: str = ""


@dataclass
class Function:
    name: str
    line: int
    col: int
    end_col: int
    params: List[Def] = field(default_factory=list)
    ret: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    container: Optional[str] = None
    depth: int = 0
    entered: bool = False
    doc: str = ""

    def signature(self, fn_kw="fn"):
        glyphs = fn_kw == "ƒ"
        ty = (lambda t: TYPE_GLYPHS.get(t, t)) if glyphs else (lambda t: t)
        ps = []
        for p in self.params:
            s = p.name + (f": {ty(p.type)}" if p.type else "")
            if p.default:
                s += (" ≔ " if glyphs else " = ") + p.default
            ps.append(s)
        r = ((" → " if glyphs else " -> ") + ty(self.ret)) if self.ret else ""
        return f"{fn_kw} {self.name}({', '.join(ps)}){r}"


@dataclass
class Struct:
    name: str
    line: int
    kind: str = "struct"
    start_line: int = 0
    end_line: int = 0
    depth: int = 0
    fields: List[Def] = field(default_factory=list)
    methods: List[str] = field(default_factory=list)
    entered: bool = False


class Index:
    def __init__(self, text: str, filename="<doc>"):
        self.text = text
        self.mode = detect_mode(text)
        self.regions = embedded_regions(text)
        self.masked = mask_embedded(text, self.regions)
        self.lines = text.split("\n")
        self.lex_errors = []
        self.tokens = self._lex()
        self.defs: List[Def] = []
        self.functions: List[Function] = []
        self.structs: Dict[str, Struct] = {}
        self.refs: Dict[str, List[int]] = {}   # name -> token indexes
        self.ident_def: Dict[int, Def] = {}    # token index -> resolved def
        self.wildcard_import = False
        self.import_lines = set()   # lines that are imports: their dotted names aren't variables
        self._build()
        self.program = None
        self.parse_error = None
        self._parse()

    # ── lexing (resilient) ─────────────────────────────────────────
    def _lex(self):
        src = self.masked
        for _ in range(40):
            lx = Lexer(src, "<doc>")
            try:
                return lx.tokenize()
            except SyntaxError as e:
                m = re.search(r":(\d+):(\d+): (.*)$", str(e))
                if not m:
                    return lx.tokens
                ln, col, msg = int(m.group(1)), int(m.group(2)), m.group(3)
                self.lex_errors.append((ln, col, msg))
                lines = src.split("\n")
                if not (1 <= ln <= len(lines)) or lines[ln - 1].strip() == "":
                    return lx.tokens
                # blank the broken line (keep its indentation) and carry on
                lines[ln - 1] = ""
                src = "\n".join(lines)
        return []

    def _parse(self):
        from compiler.parser import Parser
        if self.lex_errors:
            return
        try:
            toks = Lexer(preprocess(self.text), "<doc>").tokenize()
        except SyntaxError as e:
            self.parse_error = _err_pos(str(e))
            return
        p = Parser(toks, "<doc>")
        try:
            self.program = p.parse()
        except SyntaxError as e:
            tok = p.current()
            ln, col = (tok.sline or tok.line), (tok.scol or tok.col)
            msg = re.sub(r"^<doc>:\d+:\d+: ", "", str(e))
            self.parse_error = (ln, col, msg, max(1, tok.end - tok.start) if tok.end > tok.start else 1)
        except Exception as e:  # parser bugs must never take the server down
            self.parse_error = (1, 1, f"parser crashed: {type(e).__name__}: {e}", 1)

    # ── helpers ────────────────────────────────────────────────────
    def tok_text(self, t):
        return self.text[t.start:t.end] if t.start >= 0 else t.value

    def _next(self, i, skip_nl=False):
        j = i + 1
        while j < len(self.tokens) and skip_nl and self.tokens[j].type == TT.NEWLINE:
            j += 1
        return j

    def _scope_at(self, line):
        best = None
        for k, f in enumerate(self.functions):
            if f.start_line <= line <= f.end_line:
                if best is None or self.functions[best].start_line <= f.start_line:
                    best = k
        return best

    def _read_type(self, i):
        """Read a type annotation starting at token i → (text, next index)."""
        toks = self.tokens
        parts, depth = [], 0
        while i < len(toks):
            t = toks[i]
            if t.type in (TT.IDENT, TT.NONE, TT.DOT) or (t.type == TT.STAR):
                parts.append(GLYPH_TYPES.get(self.tok_text(t), t.value if t.type == TT.IDENT else self.tok_text(t)))
            elif t.type == TT.LBRACKET:
                depth += 1
                parts.append("[")
            elif t.type == TT.RBRACKET and depth > 0:
                depth -= 1
                parts.append("]")
            elif t.type == TT.COMMA and depth > 0:
                parts.append(", ")
            else:
                break
            i += 1
        return "".join(parts) or None, i

    def _infer(self, i):
        """Infer a let's type from the first tokens of its value."""
        toks = self.tokens
        if i >= len(toks):
            return None
        t = toks[i]
        nxt = toks[i + 1].type if i + 1 < len(toks) else TT.EOF
        simple = nxt in (TT.NEWLINE, TT.EOF, TT.DEDENT)
        if t.type == TT.INTEGER and simple:
            return "int"
        if t.type == TT.FLOAT and simple:
            return "float"
        if t.type == TT.STRING and simple:
            return "str"
        if t.type in (TT.TRUE, TT.FALSE) and simple:
            return "bool"
        if t.type == TT.LBRACKET:
            return "list"
        if t.type == TT.LBRACE:
            return "dict"
        if t.type == TT.IDENT and nxt == TT.LPAREN:
            name = t.value
            if name in self.structs:
                return name
            for f in self.functions:
                if f.name == name and f.ret:
                    return f.ret
            return {"int": "int", "float": "float", "str": "str", "len": "int", "list": "list",
                    "dict": "dict", "range": "range", "sorted": "list", "input": "str"}.get(name)
        return None

    # ── index build ─────────────────────────────────────────────────
    def _build(self):
        toks = self.tokens
        depth = 0
        open_fns: List[int] = []
        open_structs: List[Struct] = []
        last_line = 1
        line_start = True
        i = 0

        def close_to(d):
            while open_fns and self.functions[open_fns[-1]].entered and self.functions[open_fns[-1]].depth >= d:
                f = self.functions[open_fns.pop()]
                f.end_line = max(f.start_line, last_line)
            while open_structs and open_structs[-1].entered and open_structs[-1].depth >= d:
                s = open_structs.pop()
                s.end_line = max(s.start_line, last_line)

        while i < len(toks):
            t = toks[i]
            if t.type == TT.INDENT:
                depth += 1
                for k in open_fns:
                    f = self.functions[k]
                    if not f.entered and f.depth == depth - 1:
                        f.entered = True
                for s in open_structs:
                    if not s.entered and s.depth == depth - 1:
                        s.entered = True
                i += 1
                line_start = True
                continue
            if t.type == TT.DEDENT:
                depth -= 1
                close_to(depth)
                i += 1
                line_start = True
                continue
            if t.type == TT.NEWLINE:
                # header line with no indented body → one-line definition
                for k in list(open_fns):
                    f = self.functions[k]
                    if not f.entered and f.start_line == last_line and \
                            (i + 1 >= len(toks) or toks[i + 1].type != TT.INDENT):
                        f.entered = True
                        f.end_line = last_line
                        open_fns.remove(k)
                i += 1
                line_start = True
                continue
            if t.type == TT.EOF:
                break
            last_line = t.sline or last_line
            after_dot = i > 0 and toks[i - 1].type == TT.DOT
            was_line_start = line_start
            line_start = False
            scope = open_fns[-1] if open_fns else None
            in_struct_body = bool(open_structs) and open_structs[-1].entered and \
                open_structs[-1].kind == "struct" and depth == open_structs[-1].depth + 1

            if t.type == TT.FN and not after_dot:
                j = i + 1
                if j < len(toks) and toks[j].type == TT.IDENT:
                    nt = toks[j]
                    in_struct = open_structs and open_structs[-1].entered and depth == open_structs[-1].depth + 1
                    f = Function(nt.value, nt.sline, nt.scol, nt.scol + (nt.end - nt.start),
                                 start_line=t.sline, end_line=t.sline, depth=depth,
                                 container=open_structs[-1].name if in_struct else None,
                                 doc=self._doc_above(t.sline))
                    fidx = len(self.functions)
                    self.functions.append(f)
                    kind = "method" if in_struct else "function"
                    d = Def(nt.value, kind, nt.sline, nt.scol, nt.scol + (nt.end - nt.start),
                            scope=scope if not in_struct else None, container=f.container, doc=f.doc)
                    self.defs.append(d)
                    self.ident_def[j] = d
                    if in_struct:
                        open_structs[-1].methods.append(nt.value)
                    j += 1
                    if j < len(toks) and toks[j].type == TT.LPAREN:
                        j = self._params(j + 1, f, fidx)
                    if j < len(toks) and toks[j].type == TT.ARROW:
                        f.ret, j = self._read_type(j + 1)
                    d.detail = f.signature()
                    d.type = f.ret
                    open_fns.append(fidx)
                    i = j
                    continue
            elif t.type in (TT.STRUCT, TT.TRAIT) and not after_dot:
                j = i + 1
                if j < len(toks) and toks[j].type == TT.IDENT:
                    nt = toks[j]
                    s = Struct(nt.value, nt.sline, "struct" if t.type == TT.STRUCT else "trait",
                               start_line=t.sline, end_line=t.sline, depth=depth)
                    self.structs[nt.value] = s
                    open_structs.append(s)
                    d = Def(nt.value, s.kind, nt.sline, nt.scol, nt.scol + (nt.end - nt.start),
                            detail=f"{self.tok_text(t)} {nt.value}", doc=self._doc_above(t.sline))
                    self.defs.append(d)
                    self.ident_def[j] = d
                    i = j + 1
                    continue
            elif t.type == TT.IMPL and not after_dot:
                # impl Trait for Struct  /  impl Struct
                j = i + 1
                names = []
                while j < len(toks) and toks[j].type in (TT.IDENT, TT.FOR):
                    if toks[j].type == TT.IDENT:
                        names.append(toks[j].value)
                    j += 1
                if names:
                    target = names[-1]
                    s = self.structs.get(target) or Struct(target, t.sline)
                    impl = Struct(target, t.sline, "impl", start_line=t.sline, end_line=t.sline, depth=depth,
                                  fields=s.fields, methods=s.methods)
                    open_structs.append(impl)
                    if target not in self.structs:
                        self.structs[target] = impl
                i = j
                continue
            elif t.type in (TT.LET, TT.CONST) and not after_dot:
                j = i + 1
                if j < len(toks) and toks[j].type == TT.IDENT:
                    nt = toks[j]
                    d = Def(nt.value, "constant" if t.type == TT.CONST else "variable", nt.sline, nt.scol,
                            nt.scol + (nt.end - nt.start), scope=scope)
                    j += 1
                    if j < len(toks) and toks[j].type == TT.COLON:
                        d.type, j = self._read_type(j + 1)
                    if j < len(toks) and toks[j].type == TT.ASSIGN:
                        if d.type is None:
                            d.type = self._infer(j + 1)
                        d.bridge = self._bridge_ctor(j + 1)
                    d.detail = f"{self.tok_text(t)} {d.name}" + (f": {d.type}" if d.type else "")
                    self.defs.append(d)
                    self.ident_def[i + 1] = d
                    i = j
                    continue
            elif t.type == TT.FOR and not after_dot:
                j = i + 1
                while j < len(toks) and toks[j].type in (TT.IDENT, TT.COMMA, TT.LPAREN, TT.RPAREN):
                    if toks[j].type == TT.IDENT:
                        nt = toks[j]
                        d = Def(nt.value, "loopvar", nt.sline, nt.scol, nt.scol + (nt.end - nt.start),
                                detail=f"loop variable {nt.value}", scope=scope)
                        self.defs.append(d)
                        self.ident_def[j] = d
                    j += 1
                i = j
                continue
            elif t.type == TT.CATCH and not after_dot:
                j = i + 1
                if j < len(toks) and toks[j].type == TT.IDENT:
                    nt = toks[j]
                    d = Def(nt.value, "variable", nt.sline, nt.scol, nt.scol + (nt.end - nt.start),
                            detail=f"caught error {nt.value}", scope=scope)
                    self.defs.append(d)
                    self.ident_def[j] = d
                i = j + 1 if j < len(toks) else j
                continue
            elif t.type == TT.IMPORT and not after_dot:
                i = self._import(i, scope)
                continue
            elif t.type == TT.FROM and not after_dot:
                i = self._from_import(i, scope)
                continue
            elif t.type == TT.IDENT and was_line_start and t.value in BRIDGE_LANGS and \
                    i + 2 < len(toks) and toks[i + 1].type == TT.DOT and toks[i + 2].type == TT.IMPORT:
                i = self._bridge_import(i, scope)
                continue
            elif t.type == TT.IDENT and was_line_start and in_struct_body and \
                    i + 1 < len(toks) and toks[i + 1].type == TT.COLON:
                d = Def(t.value, "field", t.sline, t.scol, t.scol + (t.end - t.start), container=open_structs[-1].name)
                d.type, j = self._read_type(i + 2)
                d.detail = f"{t.value}: {d.type or 'any'}"
                open_structs[-1].fields.append(d)
                self.defs.append(d)
                self.ident_def[i] = d
                i = j
                continue
            elif t.type == TT.IDENT and was_line_start and i + 1 < len(toks) and toks[i + 1].type == TT.ASSIGN:
                # bare assignment introduces a name (Python-style) if nothing defines it yet
                if not any(d.name == t.value for d in self.defs):
                    d = Def(t.value, "variable", t.sline, t.scol, t.scol + (t.end - t.start), scope=scope,
                            type=self._infer(i + 2))
                    d.detail = f"{t.value}" + (f": {d.type}" if d.type else "")
                    self.defs.append(d)
                    self.ident_def[i] = d
            elif t.type == TT.FAT_ARROW and i > 0 and toks[i - 1].type == TT.RPAREN:
                k = i - 2
                while k >= 0 and toks[k].type != TT.LPAREN:
                    if toks[k].type == TT.IDENT:
                        nt = toks[k]
                        d = Def(nt.value, "parameter", nt.sline, nt.scol, nt.scol + (nt.end - nt.start),
                                detail=f"lambda parameter {nt.value}", scope=scope)
                        self.defs.append(d)
                        self.ident_def[k] = d
                    k -= 1
            i += 1
        last_line = len(self.lines)
        close_to(-1)
        for f in self.functions:
            if f.end_line < f.start_line:
                f.end_line = f.start_line
        for s in self.structs.values():
            if s.end_line < s.start_line:
                s.end_line = s.start_line
        # function-scoped defs: fix scope for params etc. now that ranges are known
        for d in self.defs:
            if d.kind in ("variable", "constant", "loopvar") and d.scope is None:
                d.scope = self._scope_at(d.line)
                if d.scope is not None and self.functions[d.scope].start_line == d.line:
                    d.scope = None
        self._resolve()

    def _params(self, j, f, fidx):
        toks = self.tokens
        depth = 0
        while j < len(toks) and not (toks[j].type == TT.RPAREN and depth == 0):
            t = toks[j]
            if t.type in (TT.NEWLINE, TT.INDENT, TT.DEDENT, TT.EOF):
                break  # unclosed '(' — the header ends with the line
            if t.type in (TT.IDENT, TT.SELF) and depth == 0 and toks[j - 1].type in (TT.LPAREN, TT.COMMA):
                tok_idx = j
                p = Def(t.value if t.type == TT.IDENT else "self", "parameter", t.sline, t.scol,
                        t.scol + (t.end - t.start), scope=fidx)
                j += 1
                if j < len(toks) and toks[j].type == TT.COLON:
                    p.type, j = self._read_type(j + 1)
                if j < len(toks) and toks[j].type == TT.ASSIGN:
                    k = j + 1
                    start = toks[k].start if k < len(toks) else 0
                    while k < len(toks) and toks[k].type not in (TT.COMMA, TT.RPAREN, TT.NEWLINE):
                        k += 1
                    p.default = self.text[start:toks[k - 1].end].strip() if k - 1 > j else ""
                    j = k
                f.params.append(p)
                self.defs.append(p)
                self.ident_def[tok_idx] = p
                continue
            if t.type in (TT.LPAREN, TT.LBRACKET, TT.LBRACE):
                depth += 1
            elif t.type in (TT.RPAREN, TT.RBRACKET, TT.RBRACE):
                depth -= 1
            j += 1
        for p in f.params:
            p.detail = f"{p.name}" + (f": {p.type}" if p.type else "") + (f" = {p.default}" if p.default else "")
        return j + 1 if j < len(toks) and toks[j].type == TT.RPAREN else j

    def _dotted(self, j):
        toks = self.tokens
        parts, first = [], None
        while j < len(toks) and toks[j].type in (TT.IDENT, TT.DOT, TT.DCOLON) or \
                (j < len(toks) and toks[j].type in KEYWORD_TYPES and j > 0 and toks[j - 1].type == TT.DOT):
            if first is None:
                first = j
            parts.append(self.tok_text(toks[j]) if toks[j].type != TT.DCOLON else "::")
            j += 1
        return "".join(parts), first, j

    def _import(self, i, scope):
        toks = self.tokens
        self.import_lines.add(toks[i].sline)
        name, first, j = self._dotted(i + 1)
        if not name:
            return i + 1
        target = name[3:] if name.startswith("py.") else name
        alias_tok = None
        if j < len(toks) and toks[j].type == TT.AS and j + 1 < len(toks) and toks[j + 1].type == TT.IDENT:
            alias_tok = j + 1
            j += 2
        bind_idx = alias_tok if alias_tok is not None else j - 1
        bt = toks[bind_idx]
        bind = bt.value if alias_tok is not None else target.split(".")[0]
        d = Def(bind, "module", bt.sline, bt.scol, bt.scol + (bt.end - bt.start),
                detail=f"import {name}" + (f" as {bind}" if alias_tok is not None else ""), scope=scope,
                bridge=("python", target if alias_tok is not None else target.split(".")[0]))
        self.defs.append(d)
        self.ident_def[bind_idx] = d
        return j

    def _from_import(self, i, scope):
        toks = self.tokens
        self.import_lines.add(toks[i].sline)
        name, first, j = self._dotted(i + 1)
        target = name[3:] if name.startswith("py.") else name
        if j < len(toks) and toks[j].type == TT.IMPORT:
            j += 1
            while j < len(toks) and toks[j].type in (TT.IDENT, TT.COMMA, TT.STAR, TT.AS):
                t = toks[j]
                if t.type == TT.STAR:
                    self.wildcard_import = True
                if t.type == TT.IDENT and toks[j - 1].type != TT.AS:
                    bind_idx = j
                    if j + 2 < len(toks) and toks[j + 1].type == TT.AS:
                        bind_idx = j + 2
                    bt = toks[bind_idx]
                    d = Def(bt.value, "module", bt.sline, bt.scol, bt.scol + (bt.end - bt.start),
                            detail=f"from {name} import {t.value}", scope=scope, bridge=("python", f"{target}.{t.value}"))
                    self.defs.append(d)
                    self.ident_def[bind_idx] = d
                j += 1
        return j

    def _bridge_import(self, i, scope):
        toks = self.tokens
        self.import_lines.add(toks[i].sline)
        lang = toks[i].value
        name, first, j = self._dotted(i + 3)
        if not name:
            return i + 3
        alias_tok = None
        if j < len(toks) and toks[j].type == TT.AS and j + 1 < len(toks) and toks[j + 1].type == TT.IDENT:
            alias_tok = j + 1
            j += 2
        bind_idx = alias_tok if alias_tok is not None else j - 1
        bt = toks[bind_idx]
        bind = bt.value if alias_tok is not None else re.split(r"[.:]", name)[-1]
        real_lang = "python" if lang in ("sym", "python") else lang
        d = Def(bind, "module", bt.sline, bt.scol, bt.scol + (bt.end - bt.start),
                detail=f"{lang}.import {name}" + (f" as {bind}" if alias_tok is not None else ""),
                scope=scope, bridge=(real_lang, name))
        self.defs.append(d)
        self.ident_def[bind_idx] = d
        return j

    def _bridge_ctor(self, j):
        """let x = Calculator()  where Calculator is a bridge import → x is a live handle."""
        toks = self.tokens
        if j + 1 < len(toks) and toks[j].type == TT.IDENT and toks[j + 1].type == TT.LPAREN:
            for d in self.defs:
                if d.name == toks[j].value and d.bridge:
                    return (d.bridge[0], d.bridge[1], "instance")
        return None

    def _doc_above(self, line1):
        """Comment lines directly above a definition are its documentation."""
        out = []
        k = line1 - 2
        while k >= 0:
            s = self.lines[k].strip()
            if s.startswith("#") and s not in ("#compact", "#symbol"):
                out.append(s.lstrip("#").strip())
                k -= 1
            else:
                break
        return "\n".join(reversed(out))

    # ── resolution ──────────────────────────────────────────────────
    def visible_defs(self, line, name=None):
        sc = self._scope_at(line)
        chain = set()
        while sc is not None:
            chain.add(sc)
            parent = None
            f = self.functions[sc]
            for k, g in enumerate(self.functions):
                if k != sc and g.start_line <= f.start_line and f.end_line <= g.end_line and \
                        (parent is None or self.functions[parent].start_line <= g.start_line):
                    parent = k
            sc = parent
        out = []
        for d in self.defs:
            if name is not None and d.name != name:
                continue
            if d.kind in ("field", "method"):
                continue
            if d.scope is None or d.scope in chain:
                out.append(d)
        return out

    def resolve(self, tok_index):
        if tok_index in self.ident_def:
            return self.ident_def[tok_index]
        t = self.tokens[tok_index]
        name = t.value if t.type == TT.IDENT else ("self" if t.type == TT.SELF else None)
        if name is None:
            return None
        if tok_index > 0 and self.tokens[tok_index - 1].type == TT.DOT:
            owner = self.receiver_type(tok_index - 2)
            if owner and owner in self.structs:
                for f in self.structs[owner].fields:
                    if f.name == name:
                        return f
                for d in self.defs:
                    if d.kind == "method" and d.container == owner and d.name == name:
                        return d
            return None
        cands = self.visible_defs(t.sline, name)
        if not cands:
            return None
        # nearest preceding definition wins (shadowing), else the first
        before = [d for d in cands if d.line <= t.sline]
        return max(before, key=lambda d: (d.scope is not None, d.line)) if before else cands[0]

    def receiver_type(self, k):
        """Type of the expression ending at token k (simple cases: names, self, calls)."""
        if k < 0:
            return None
        t = self.tokens[k]
        if t.type == TT.SELF:
            for f in self.functions:
                if f.start_line <= t.sline <= f.end_line and f.container:
                    return f.container
            return None
        if t.type == TT.IDENT:
            d = self.resolve(k)
            if d is None:
                return t.value if t.value in self.structs else None
            if d.kind in ("struct", "trait"):
                return d.name
            return d.type
        if t.type == TT.RPAREN:
            depth, j = 0, k
            while j >= 0:
                if self.tokens[j].type == TT.RPAREN:
                    depth += 1
                elif self.tokens[j].type == TT.LPAREN:
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            if j > 0 and self.tokens[j - 1].type == TT.IDENT:
                name = self.tokens[j - 1].value
                if name in self.structs:
                    return name
                for f in self.functions:
                    if f.name == name:
                        return f.ret
        return None

    def _resolve(self):
        for i, t in enumerate(self.tokens):
            if t.type == TT.IDENT:
                self.refs.setdefault(t.value, []).append(i)

    def token_at(self, line1, col1):
        """Token covering 1-based line / code-point column (or ending right there)."""
        best = None
        for i, t in enumerate(self.tokens):
            if t.type in STRUCTURAL or t.start < 0:
                continue
            if t.sline != line1:
                if t.sline > line1:
                    break
                continue
            end_col = t.scol + (t.end - t.start)
            if t.scol <= col1 < end_col:
                return i
            if end_col == col1:
                best = i
        return best

    def function_by_name(self, name):
        for f in self.functions:
            if f.name == name and f.container is None:
                return f
        for f in self.functions:
            if f.name == name:
                return f
        return None


def _err_pos(msg):
    m = re.search(r":(\d+):(\d+): (.*)$", msg)
    if m:
        return int(m.group(1)), int(m.group(2)), m.group(3), 1
    return 1, 1, msg, 1

"""
Sym syntax modes — one table, three spellings.

Normal, compact and symbol are three spellings of ONE token stream, so
switching a file between them is a token-level rewrite: every keyword and
operator token is re-spelled in place, and everything else — comments,
spacing, strings, embedded js>/rust>/... blocks — is left byte-for-byte alone.

    from compiler.modes import morph
    text = morph(src, "symbol")          # raises MorphError if not lossless

morph() proves itself: it re-parses the result and compares the AST to the
original. A rewrite that would change meaning is refused, never shipped.
"""

from .lexer import Lexer, TT
from .preprocess import embedded_regions, mask_embedded, preprocess

MODES = ("normal", "compact", "symbol")

# token type -> (normal, compact, symbol)
SPELLING = {
    TT.FN:        ("fn", "f", "ƒ"),
    TT.LET:       ("let", "@", "∂"),
    TT.CONST:     ("const", "const", "≡"),
    TT.RETURN:    ("return", "<-", "←"),
    TT.IF:        ("if", "?", "?"),
    TT.ELIF:      ("elif", "??", "??"),
    TT.ELSE:      ("else", "!", "!"),
    TT.FOR:       ("for", "~", "∀"),
    TT.WHILE:     ("while", "~~", "⟳"),
    TT.IN:        ("in", "in", "∈"),
    TT.BREAK:     ("break", "break", "↯"),
    TT.CONTINUE:  ("continue", "continue", "↻"),
    TT.MATCH:     ("match", "match", "⋈"),
    TT.TRY:       ("try", "try", "⊡"),
    TT.CATCH:     ("catch", "catch", "⊠"),
    TT.IMPORT:    ("import", "<<", "⊂"),
    TT.FROM:      ("from", "from", "⊃"),
    TT.AS:        ("as", "as", "↦"),
    TT.PRINT:     ("print", ">>", "⊕"),
    TT.STRUCT:    ("struct", "struct", "∏"),
    TT.TRAIT:     ("trait", "trait", "◇"),
    TT.IMPL:      ("impl", "impl", "⊢"),
    TT.ASYNC:     ("async", "async", "⇝"),
    TT.AWAIT:     ("await", "await", "⧗"),
    TT.AND:       ("and", "and", "∧"),
    TT.OR:        ("or", "or", "∨"),
    TT.NOT:       ("not", "not", "¬"),
    TT.TRUE:      ("true", "true", "⊤"),
    TT.FALSE:     ("false", "false", "⊥"),
    TT.NONE:      ("none", "none", "∅"),
    TT.ARROW:     ("->", "->", "→"),
    TT.LTE:       ("<=", "<=", "≤"),
    TT.GTE:       (">=", ">=", "≥"),
    TT.NEQ:       ("!=", "!=", "≠"),
    TT.ASSIGN:    ("=", "=", "≔"),
    TT.FAT_ARROW: ("=>", "=>", "⇒"),
    TT.PIPE:      ("|>", "|>", "▷"),
}

# type names, spelled as glyphs only in symbol mode (annotation positions only)
TYPE_GLYPHS = {"int": "ℤ", "float": "ℝ", "bool": "𝔹", "str": "𝕊", "list": "𝕃", "dict": "𝔻"}
GLYPH_TYPES = {v: k for k, v in TYPE_GLYPHS.items()}

# The keywords whose spelling is the same word in every mode stay words in the
# lexer too (sort, optimize, benchmark, self) — no glyph would read better.


class MorphError(ValueError):
    def __init__(self, msg, line=None):
        super().__init__(msg if line is None else f"line {line}: {msg}")
        self.line = line


def detect_mode(src: str) -> str:
    first = src.split("\n", 1)[0].strip()
    return first[1:] if first in ("#compact", "#symbol") else "normal"


def glyph_table():
    """The table editors use for live transliteration (served by `sym lsp`)."""
    rows = []
    for tt, (n, c, s) in SPELLING.items():
        rows.append({"token": tt.name.lower(), "normal": n, "compact": c, "symbol": s,
                     "word": n.isalpha()})
    types = [{"name": k, "symbol": v} for k, v in TYPE_GLYPHS.items()]
    return {"modes": list(MODES), "tokens": rows, "types": types}


def _lex(src, mode):
    masked = mask_embedded(src)
    return Lexer(masked, "<morph>").tokenize()


def _is_annotation_ident(tokens, i):
    """IDENT at i sits in a type position: after ':' or '->' (or a ',' / '[' inside one)."""
    k = i - 1
    while k >= 0 and tokens[k].type in (TT.LBRACKET, TT.COMMA):
        if tokens[k].type == TT.COMMA:
            # walk back through a generic arg list: list[int, str]
            depth = 0
            j = k
            while j >= 0:
                if tokens[j].type == TT.RBRACKET:
                    depth += 1
                elif tokens[j].type == TT.LBRACKET:
                    if depth == 0:
                        return _is_annotation_ident(tokens, j)
                    depth -= 1
                elif tokens[j].type in (TT.NEWLINE, TT.LPAREN, TT.COLON, TT.ARROW):
                    break
                j -= 1
            return False
        k -= 1
    return k >= 0 and tokens[k].type in (TT.COLON, TT.ARROW)


_BRIDGE_IMPORT = None


def _frozen_lines(src):
    """Lines the preprocessor reads as raw text (lang.import X as Y) — never respelled."""
    import re
    global _BRIDGE_IMPORT
    if _BRIDGE_IMPORT is None:
        _BRIDGE_IMPORT = re.compile(r'^[ \t]*(java|js|php|ruby|perl|c|r|sym)\.import[ \t]')
    return {n for n, line in enumerate(src.split("\n"), 1) if _BRIDGE_IMPORT.match(line)}


def _respell(src, tokens, target, spaced):
    tidx = MODES.index(target)
    frozen = _frozen_lines(src)
    out, last = [], 0
    prev_end = None
    for i, t in enumerate(tokens):
        if t.start < 0 or t.end <= t.start or t.type in (TT.NEWLINE, TT.INDENT, TT.DEDENT, TT.EOF, TT.DIRECTIVE):
            continue
        if t.sline in frozen:
            prev_end = t.end
            continue
        text = src[t.start:t.end]
        new = None
        after_dot = i > 0 and tokens[i - 1].type == TT.DOT
        if t.type in SPELLING and not after_dot:
            spellings = SPELLING[t.type]
            if target == "normal" and text in ("True", "False", "None"):
                new = text  # keep Python-style capitalisation in normal mode
            else:
                new = spellings[tidx]
        elif t.type == TT.IDENT and _is_annotation_ident(tokens, i):
            name = GLYPH_TYPES.get(text, text)
            if target == "symbol" and name in TYPE_GLYPHS:
                new = TYPE_GLYPHS[name]
            elif target != "symbol" and text in GLYPH_TYPES:
                new = name
        if new is None:
            prev_end = t.end
            continue
        out.append(src[last:t.start])
        # a word next to a word needs a space; an operator glued to another operator
        # can re-lex differently (a<-1 vs a <- 1) — in spaced mode, separate them.
        if spaced and prev_end == t.start and out and out[-1][-1:] not in (" ", "\n", "(", "["):
            out.append(" ")
        out.append(new)
        last = t.end
        if spaced and i + 1 < len(tokens) and tokens[i + 1].start == t.end and \
                tokens[i + 1].type not in (TT.NEWLINE, TT.EOF, TT.RPAREN, TT.RBRACKET, TT.COMMA, TT.COLON):
            out.append(src[last:last])  # placeholder (spacing added before next token)
        prev_end = t.end
    out.append(src[last:])
    body = "".join(out)
    return body


def _with_directive(body_lines_src, target):
    if target == "normal":
        return body_lines_src
    return f"#{target}\n" + body_lines_src


def _strip_directive(src):
    mode = detect_mode(src)
    if mode == "normal":
        return src, mode
    nl = src.find("\n")
    return ("" if nl < 0 else src[nl + 1:]), mode


_OP_CANON = {"≤": "<=", "≥": ">=", "≠": "!=", "≔": "=", "→": "->", "⇒": "=>", "▷": "|>",
             "∧": "and", "∨": "or", "¬": "not"}


def _check_collisions(src, tokens, target):
    from .lexer import NORMAL_KEYWORDS, COMPACT_KEYWORDS
    kw = NORMAL_KEYWORDS if target == "normal" else COMPACT_KEYWORDS
    for i, t in enumerate(tokens):
        if t.type == TT.IDENT and t.start >= 0:
            text = src[t.start:t.end]
            if text in kw and not (i > 0 and tokens[i - 1].type == TT.DOT):
                raise MorphError(f"identifier '{text}' is a keyword in {target} mode — rename it first",
                                 line=t.sline)


def ast_signature(node):
    """Structural fingerprint of an AST, ignoring positions and spelling."""
    if isinstance(node, str):
        return _OP_CANON.get(node, GLYPH_TYPES.get(node, node))
    if isinstance(node, list):
        return [ast_signature(x) for x in node]
    if isinstance(node, dict):
        return {k: ast_signature(v) for k, v in node.items()}
    if hasattr(node, "__dataclass_fields__"):
        d = {"_": type(node).__name__}
        for k in node.__dataclass_fields__:
            if k in ("line", "col", "mode"):
                continue
            d[k] = ast_signature(getattr(node, k))
        return d
    return node


def parse_source(src):
    from .parser import Parser
    toks = Lexer(preprocess(src), "<morph>").tokenize()
    return Parser(toks, "<morph>").parse()


def morph(src: str, target: str, verify: bool = True) -> str:
    """Re-spell a whole file in `target` mode. Lossless or MorphError."""
    if target not in MODES:
        raise MorphError(f"unknown mode '{target}' (normal | compact | symbol)")
    body, source_mode = _strip_directive(src)
    if source_mode == target:
        return src
    # lex the body in the SOURCE mode (directive re-attached, then offsets shifted)
    prefix = "" if source_mode == "normal" else f"#{source_mode}\n"
    full = prefix + body
    tokens = _lex(full, source_mode)
    off = len(prefix)
    errors = []
    original_sig = None
    if verify:
        try:
            original_sig = ast_signature(parse_source(full).statements)
        except SyntaxError as e:
            raise MorphError(f"source does not parse: {e}")
    _check_collisions(full, tokens, target)
    for spaced in (False, True):
        new_body = _respell(full, tokens, target, spaced)[off:]
        result = _with_directive(new_body, target)
        if not verify:
            return result
        try:
            sig = ast_signature(parse_source(result).statements)
        except SyntaxError as e:
            errors.append(str(e))
            continue
        if sig == original_sig:
            return result
        errors.append(_first_divergence(original_sig, sig))
    raise MorphError(f"cannot express this file in {target} mode without changing its meaning: {errors[-1]}")


def _first_divergence(a, b, path="program"):
    if type(a) != type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        if a.get("_") != b.get("_"):
            return f"{path}: {a.get('_')} became {b.get('_')}"
        for k in a:
            if a[k] != b.get(k):
                return _first_divergence(a[k], b.get(k), f"{path}.{a.get('_', '')}.{k}")
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items became {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return _first_divergence(x, y, f"{path}[{i}]")
    return f"{path}: {a!r} became {b!r}"

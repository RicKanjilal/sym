"""
sym lsp — the Sym language server.

Standard LSP features for .sym files, plus Sym-only extensions:

  sym/glyphTable        the normal/compact/symbol spelling table (live transliteration)
  sym/morph             re-spell a whole file in another mode (lossless or refused)
  sym/purity            per-function native-C verdicts with reasons
  sym/emit              generated Python / C / hybrid glue, function-linked
  sym/regions           embedded js>/java>/rust>/... blocks (for language injection)
  sym/describe          reflect a library in any runtime (java, js, python, ruby, php, perl, r)
  sym/foreignCompletion completions for Sym bridge calls inside NON-Sym files
                        (lucene = sym.java.package("org.apache.lucene"); lucene.|)
  sym/foreignHover      hover for the same
"""

import os
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlparse

_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lsp.jsonrpc import Transport, LspError  # noqa: E402
from lsp.text import Document, cp_to_u16  # noqa: E402
from lsp.index import (Index, SYM_BUILTINS, PY_BUILTINS, KNOWN_TYPES, KEYWORD_TYPES,  # noqa: E402
                       OPERATOR_TYPES, STRUCTURAL)
from compiler.lexer import TT, NORMAL_KEYWORDS, COMPACT_KEYWORDS  # noqa: E402
from compiler.modes import (SPELLING, MODES, TYPE_GLYPHS, GLYPH_TYPES, glyph_table, morph,  # noqa: E402
                            MorphError)

VERSION = "0.3.0"

# ── LSP enums ──────────────────────────────────────────────────────────
CK = dict(text=1, method=2, function=3, constructor=4, field=5, variable=6, cls=7, interface=8, module=9,
          property=10, value=12, enum=13, keyword=14, snippet=15, constant=21, struct=22, operator=24,
          typeparam=25, enum_member=20, folder=19)
SK = dict(module=2, namespace=3, package=4, cls=5, method=6, property=7, field=8, constructor=9,
          interface=11, function=12, variable=13, constant=14, struct=23)
SEM_TYPES = ["namespace", "type", "class", "struct", "parameter", "variable", "property", "function",
             "method", "keyword", "number", "operator", "decorator", "enumMember", "string", "comment"]
SEM_MODS = ["declaration", "readonly", "defaultLibrary", "native", "python", "bridge", "static", "glyph"]
_ST = {n: i for i, n in enumerate(SEM_TYPES)}
_SM = {n: 1 << i for i, n in enumerate(SEM_MODS)}

KIND_TO_CK = {"function": CK["function"], "method": CK["method"], "struct": CK["struct"],
              "trait": CK["interface"], "field": CK["field"], "variable": CK["variable"],
              "constant": CK["constant"], "parameter": CK["variable"], "module": CK["module"],
              "loopvar": CK["variable"], "class": CK["cls"], "package": CK["folder"],
              "constructor": CK["constructor"], "enum": CK["enum_member"], "namespace": CK["module"],
              "interface": CK["interface"]}
KIND_TO_SK = {"function": SK["function"], "method": SK["method"], "struct": SK["struct"],
              "trait": SK["interface"], "field": SK["field"], "variable": SK["variable"],
              "constant": SK["constant"], "module": SK["module"]}

KEYWORD_DOCS = {
    TT.FN: "Define a function.", TT.LET: "Bind a variable.", TT.CONST: "Bind a constant.",
    TT.RETURN: "Return from the function.", TT.IF: "Conditional.", TT.ELIF: "Else-if branch.",
    TT.ELSE: "Else branch.", TT.FOR: "Loop over an iterable.", TT.WHILE: "Loop while a condition holds.",
    TT.IN: "Membership / loop source.", TT.BREAK: "Leave the loop.", TT.CONTINUE: "Next iteration.",
    TT.MATCH: "Pattern match.", TT.TRY: "Guarded block.", TT.CATCH: "Handle an error.",
    TT.IMPORT: "Import a module.", TT.FROM: "Import names from a module.", TT.AS: "Alias.",
    TT.PRINT: "Print.", TT.STRUCT: "Define a struct (product type).", TT.TRAIT: "Define a trait.",
    TT.IMPL: "Implement methods for a type.", TT.ASYNC: "Asynchronous function.", TT.AWAIT: "Await.",
    TT.AND: "Logical and.", TT.OR: "Logical or.", TT.NOT: "Logical not.", TT.TRUE: "Boolean true.",
    TT.FALSE: "Boolean false.", TT.NONE: "No value.", TT.ARROW: "Return type.", TT.LTE: "Less or equal.",
    TT.GTE: "Greater or equal.", TT.NEQ: "Not equal.", TT.ASSIGN: "Assignment.",
    TT.FAT_ARROW: "Lambda / match arm.", TT.PIPE: "Pipe: x |> f is f(x).",
}

SNIPPETS = {
    "normal": [
        ("fn", "fn ${1:name}(${2:n}: ${3:int}) -> ${4:int}\n    ${0:return n}", "function"),
        ("struct", "struct ${1:Name}\n    ${2:field}: ${3:int}\n\n    fn ${4:method}(self) -> ${5:int}\n        ${0:return self.${2}}", "struct"),
        ("for", "for ${1:i} in ${2:0..10}\n    ${0}", "for loop"),
        ("while", "while ${1:cond}\n    ${0}", "while loop"),
        ("if", "if ${1:cond}\n    ${0}", "if"),
        ("ifelse", "if ${1:cond}\n    ${2}\nelse\n    ${0}", "if / else"),
        ("try", "try\n    ${1}\ncatch ${2:e}\n    ${0:print e}", "try / catch"),
        ("match", "match ${1:value}\n    ${2:1} => ${0}", "match"),
    ],
    "compact": [
        ("f", "f ${1:name}(${2:n}: ${3:int}) -> ${4:int}\n    ${0:<- n}", "function"),
        ("~", "~ ${1:i} in ${2:0..10}\n    ${0}", "for loop"),
        ("?", "? ${1:cond}\n    ${0}", "if"),
    ],
    "symbol": [
        ("ƒ", "ƒ ${1:name}(${2:n}: ℤ) → ${3:ℤ}\n    ${0:← n}", "function"),
        ("∀", "∀ ${1:i} ∈ ${2:0..10}\n    ${0}", "for loop"),
        ("⟳", "⟳ ${1:cond}\n    ${0}", "while loop"),
        ("∏", "∏ ${1:Name}\n    ${2:field}: ℤ", "struct"),
    ],
}
BLOCK_SNIPPETS = [(f"{lang}>", f"{lang}> {{\n    ${{0}}\n}}", f"{label} block — runs in a real {label} runtime")
                  for lang, label in (("js", "JavaScript"), ("python", "Python"), ("java", "Java"), ("rust", "Rust"),
                                      ("go", "Go"), ("ruby", "Ruby"), ("php", "PHP"), ("r", "R"), ("perl", "Perl"))]
BRIDGE_IMPORT_SNIPPETS = [(f"{lang}.import", f"{lang}.import ${{1:target}}", f"import from {label}")
                          for lang, label in (("java", "the JVM"), ("js", "npm / Node"), ("sym", "any registry"),
                                              ("ruby", "RubyGems"), ("php", "PHP"), ("r", "CRAN"), ("perl", "CPAN"))]


def uri_to_path(uri):
    p = urlparse(uri)
    return unquote(p.path) if p.scheme == "file" else uri


def _md(code, lang="sym"):
    return f"```{lang}\n{code}\n```"


class Server:
    def __init__(self, transport):
        self.t = transport
        self.docs = {}
        self.root = None
        self.shutdown = False
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sym-lsp")
        self._purity_cache = {}
        self._ws_index = None
        self._py_modules = None
        self._client_caps = {}
        self._current_id = None

    # ── loop ───────────────────────────────────────────────────────────
    def run(self):
        while True:
            msg = self.t.read()
            if msg is None:
                break
            try:
                self.dispatch(msg)
            except Exception:
                traceback.print_exc(file=sys.stderr)
            if msg.get("method") == "exit":
                break
        self.pool.shutdown(wait=False, cancel_futures=True)

    def dispatch(self, msg):
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        if method is None:
            return  # a response to something we sent
        handler = getattr(self, "m_" + method.replace("/", "_").replace("$", "S"), None)
        if mid is None:
            if handler:
                handler(params)
            return
        if handler is None:
            self.t.respond(mid, error={"code": -32601, "message": f"method not found: {method}"})
            return
        try:
            result = handler(params)
            if result is _ASYNC:
                return
            self.t.respond(mid, result)
        except LspError as e:
            self.t.respond(mid, error={"code": e.code, "message": e.message})
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            self.t.respond(mid, error={"code": -32603, "message": f"{type(e).__name__}: {e}"})

    def async_respond(self, mid, fn):
        def run():
            try:
                self.t.respond(mid, fn())
            except LspError as e:
                self.t.respond(mid, error={"code": e.code, "message": e.message})
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                self.t.respond(mid, error={"code": -32603, "message": f"{type(e).__name__}: {e}"})
        self.pool.submit(run)
        return _ASYNC

    # ── lifecycle ──────────────────────────────────────────────────────
    def m_initialize(self, p):
        root = p.get("rootUri") or (p.get("workspaceFolders") or [{}])[0].get("uri")
        self.root = uri_to_path(root) if root else p.get("rootPath")
        self._client_caps = p.get("capabilities") or {}
        return {
            "serverInfo": {"name": "sym-lsp", "version": VERSION},
            "capabilities": {
                "positionEncoding": "utf-16",
                "textDocumentSync": {"openClose": True, "change": 2, "save": {"includeText": False}},
                "completionProvider": {"triggerCharacters": [".", ">", " ", "("], "resolveProvider": False},
                "hoverProvider": True,
                "signatureHelpProvider": {"triggerCharacters": ["(", ","], "retriggerCharacters": [","]},
                "definitionProvider": True,
                "typeDefinitionProvider": True,
                "referencesProvider": True,
                "documentHighlightProvider": True,
                "documentSymbolProvider": True,
                "workspaceSymbolProvider": True,
                "renameProvider": {"prepareProvider": True},
                "codeActionProvider": {"codeActionKinds": ["quickfix", "refactor.rewrite"]},
                "codeLensProvider": {"resolveProvider": False},
                "documentFormattingProvider": True,
                "foldingRangeProvider": True,
                "inlayHintProvider": True,
                "semanticTokensProvider": {"legend": {"tokenTypes": SEM_TYPES, "tokenModifiers": SEM_MODS},
                                           "full": True, "range": False},
                "executeCommandProvider": {"commands": ["sym.morph"]},
                "experimental": {"symGlyphTable": True, "symMorph": True, "symPurity": True, "symEmit": True,
                                 "symDescribe": True, "symForeign": True},
            },
        }

    def m_initialized(self, p):
        pass

    def m_shutdown(self, p):
        self.shutdown = True
        return None

    def m_exit(self, p):
        pass

    def m_SScancelRequest(self, p):
        pass

    def m_SSsetTrace(self, p):
        pass

    def m_workspace_didChangeConfiguration(self, p):
        pass

    def m_workspace_didChangeWatchedFiles(self, p):
        self._ws_index = None

    # ── documents ──────────────────────────────────────────────────────
    def m_textDocument_didOpen(self, p):
        td = p["textDocument"]
        self.docs[td["uri"]] = Document(td["uri"], td["text"], td.get("version", 0), td.get("languageId", "sym"))
        self.publish(td["uri"])

    def m_textDocument_didChange(self, p):
        doc = self.docs.get(p["textDocument"]["uri"])
        if doc is None:
            return
        doc.apply_changes(p["contentChanges"], p["textDocument"].get("version", doc.version + 1))
        self.publish(doc.uri)

    def m_textDocument_didSave(self, p):
        self._ws_index = None

    def m_textDocument_didClose(self, p):
        uri = p["textDocument"]["uri"]
        self.docs.pop(uri, None)
        self.t.notify("textDocument/publishDiagnostics", {"uri": uri, "diagnostics": []})

    def doc(self, p):
        uri = p["textDocument"]["uri"]
        d = self.docs.get(uri)
        if d is None:
            path = uri_to_path(uri)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    d = Document(uri, f.read())
            else:
                raise LspError(-32602, f"unknown document {uri}")
        return d

    def ix(self, doc) -> Index:
        if doc._analysis is None:
            doc._analysis = Index(doc.text, uri_to_path(doc.uri))
        return doc._analysis

    def is_sym(self, doc):
        return doc.language_id == "sym" or doc.uri.endswith(".sym")

    # ── diagnostics ────────────────────────────────────────────────────
    def publish(self, uri):
        doc = self.docs.get(uri)
        if doc is None or not self.is_sym(doc):
            return
        diags = self.diagnostics(doc)
        self.t.notify("textDocument/publishDiagnostics", {"uri": uri, "version": doc.version, "diagnostics": diags})
        # purity changes with every edit — push it so lenses/gutters stay live
        try:
            self.t.notify("sym/purityChanged", {"uri": uri, "version": doc.version, "functions": self.purity(doc)})
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def diagnostics(self, doc):
        ix = self.ix(doc)
        out = []

        def add(rng, msg, sev, code=None, tags=None, data=None):
            d = {"range": rng, "message": msg, "severity": sev, "source": "sym"}
            if code:
                d["code"] = code
            if tags:
                d["tags"] = tags
            if data is not None:
                d["data"] = data
            out.append(d)

        for ln, col, msg in ix.lex_errors:
            add(doc.range(ln, max(1, col - 1), ln, max(2, col)), msg, 1, "syntax")
        if ix.parse_error and not ix.lex_errors:
            ln, col, msg, width = ix.parse_error
            add(doc.range(ln, col, ln, col + width), msg, 1, "syntax")
        if ix.program is not None:
            try:
                from compiler.typecheck import typecheck
                for w in typecheck(ix.program):
                    m = re.match(r"line (\d+): (.*)", w)
                    if m:
                        add(doc.line_range(int(m.group(1))), m.group(2), 2, "type")
            except Exception:
                pass
        headers = [(r["start"], r["body_start"]) for r in ix.regions]
        if not ix.wildcard_import:
            kw_other = NORMAL_KEYWORDS if ix.mode != "normal" else {}
            for i, t in enumerate(ix.tokens):
                if t.type != TT.IDENT or t.start < 0:
                    continue
                prev = ix.tokens[i - 1].type if i else None
                nxt = ix.tokens[i + 1].type if i + 1 < len(ix.tokens) else None
                if prev in (TT.DOT, TT.COLON, TT.ARROW, TT.AT, TT.DCOLON, TT.LBRACKET) or \
                        (prev in (TT.LPAREN, TT.COMMA) and nxt == TT.ASSIGN):
                    continue
                name = t.value
                if any(a <= t.start < b for a, b in headers):
                    continue
                if t.sline in ix.import_lines and ix.ident_def.get(i) is None:
                    continue
                if name in SYM_BUILTINS or name in PY_BUILTINS or name in KNOWN_TYPES or name in ix.structs \
                        or name in ("sym", "self", "_") or name.startswith("__"):
                    continue
                if ix.resolve(i) is not None:
                    continue
                if prev == TT.COMMA and _in_type_brackets(ix, i):
                    continue
                rng = doc.range(t.sline, t.scol, t.sline, t.scol + (t.end - t.start))
                if name in kw_other:
                    want = SPELLING.get(kw_other[name], (None, None, None))[MODES.index(ix.mode)]
                    add(rng, f"'{name}' is not a keyword in {ix.mode} mode" + (f" — write '{want}'" if want else ""),
                        1, "mode-keyword", data={"replace": want} if want else None)
                else:
                    add(rng, f"'{name}' is not defined", 2, "undefined-name")
            # unused locals
            for d in ix.defs:
                if d.kind not in ("variable", "constant") or d.scope is None:
                    continue
                f = ix.functions[d.scope]
                used = any(ix.tokens[k].sline != d.line or ix.tokens[k].scol != d.col
                           for k in ix.refs.get(d.name, [])
                           if f.start_line <= ix.tokens[k].sline <= f.end_line)
                if not used:
                    add(doc.range(d.line, d.col, d.line, d.end_col), f"'{d.name}' is never used", 4, "unused", [1])
        return out

    # ── purity / emit ──────────────────────────────────────────────────
    def purity(self, doc):
        ix = self.ix(doc)
        if ix.program is None:
            return self._purity_cache.get(doc.uri, [])
        from compiler.purity import purity_report
        rep = purity_report(ix.program)
        by_name = {}
        for f in ix.functions:
            by_name.setdefault(f.name, f)
        out = []
        for e in rep:
            f = by_name.get(e["name"])
            line = f.line if f else e["line"]
            col = f.col if f else 1
            end_col = f.end_col if f else 1
            item = {"name": e["name"], "native": e["native"], "reason": e["reason"],
                    "range": doc.range(line, col, line, end_col),
                    "bodyRange": doc.range(f.start_line, 1, f.end_line, len(doc.lines[f.end_line - 1]) + 1) if f else None,
                    "reasonLine": (e["reason_line"] - 1) if e.get("reason_line") else None}
            out.append(item)
        self._purity_cache[doc.uri] = out
        return out

    def m_sym_purity(self, p):
        doc = self.doc(p)
        return {"functions": self.purity(doc), "stale": self.ix(doc).program is None}

    def m_sym_emit(self, p):
        doc = self.doc(p)
        ix = self.ix(doc)
        target = p.get("target", "python")
        if ix.program is None:
            raise LspError(-32001, "file does not parse — fix the errors to see generated code")
        name = os.path.basename(uri_to_path(doc.uri))
        if target == "python":
            from compiler.codegen_python import generate_python
            code = generate_python(ix.program, name)
            lang = "python"
        else:
            from compiler.hybrid import HybridCodegen
            c_src, py_src, pure = HybridCodegen().generate(ix.program)
            code, lang = (c_src, "c") if target == "c" else (py_src, "python")
        lines = code.split("\n")
        links = []
        for f in ix.functions:
            if lang == "python":
                pat = re.compile(r"^\s*(?:async\s+)?def\s+" + re.escape(f.name) + r"\s*\(")
            else:
                pat = re.compile(r"^[A-Za-z_][\w\s\*]*\b" + re.escape(f.name) + r"\s*\([^;]*$")
            for k, l in enumerate(lines):
                if pat.match(l):
                    end = k
                    for m in range(k + 1, len(lines)):
                        if lines[m].strip() and not lines[m].startswith((" ", "\t", "}")):
                            break
                        end = m
                    links.append({"name": f.name, "symStart": f.start_line - 1, "symEnd": f.end_line - 1,
                                  "outStart": k, "outEnd": end})
                    break
        return {"code": code, "language": lang, "links": links}

    # ── modes ──────────────────────────────────────────────────────────
    def m_sym_glyphTable(self, p):
        return glyph_table()

    def m_sym_morph(self, p):
        doc = self.doc(p)
        try:
            return {"text": morph(doc.text, p["to"])}
        except MorphError as e:
            raise LspError(-32002, str(e))

    def m_sym_regions(self, p):
        doc = self.doc(p)
        ix = self.ix(doc)
        return [{"lang": r["lang"], "range": doc.offset_range(r["start"], r["end"]),
                 "bodyRange": doc.offset_range(r["body_start"], r["body_end"])} for r in ix.regions]

    def m_workspace_executeCommand(self, p):
        if p.get("command") == "sym.morph":
            uri, to = p["arguments"][0], p["arguments"][1]
            doc = self.doc({"textDocument": {"uri": uri}})
            try:
                new = morph(doc.text, to)
            except MorphError as e:
                raise LspError(-32002, str(e))
            self.t.write({"jsonrpc": "2.0", "id": f"edit-{uri}", "method": "workspace/applyEdit",
                          "params": {"label": f"Convert to {to} mode",
                                     "edit": {"changes": {uri: [_full_edit(doc, new)]}}}})
        return None

    # ── semantic tokens ────────────────────────────────────────────────
    def m_textDocument_semanticTokens_full(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return {"data": []}
        ix = self.ix(doc)
        native = {f["name"] for f in self.purity(doc) if f["native"]}
        pyfn = {f["name"] for f in self.purity(doc) if not f["native"]}
        data = []
        prev_line, prev_char = 0, 0
        toks = ix.tokens
        for i, t in enumerate(toks):
            if t.type in STRUCTURAL or t.start < 0 or t.end <= t.start:
                continue
            text = doc.text[t.start:t.end]
            ttype, mods = None, 0
            prev = toks[i - 1].type if i else None
            nxt = toks[i + 1].type if i + 1 < len(toks) else None
            if t.type in KEYWORD_TYPES and prev != TT.DOT:
                if t.type in (TT.TRUE, TT.FALSE, TT.NONE):
                    ttype = "enumMember"
                elif t.type in OPERATOR_TYPES or t.type in (TT.ARROW, TT.LTE, TT.GTE, TT.NEQ, TT.ASSIGN,
                                                             TT.FAT_ARROW, TT.PIPE):
                    ttype = "operator"
                else:
                    ttype = "keyword"
                if len(text) == 1 and ord(text) > 127 or (len(text) == 2 and ord(text[0]) > 0xD7FF):
                    mods |= _SM["glyph"]
            elif t.type in OPERATOR_TYPES:
                ttype = "operator"
                if any(ord(c) > 127 for c in text):
                    mods |= _SM["glyph"]
            elif t.type in (TT.INTEGER, TT.FLOAT):
                ttype = "number"
            elif t.type == TT.DIRECTIVE:
                ttype = "decorator"
            elif t.type == TT.IDENT:
                if text in GLYPH_TYPES:
                    ttype, mods = "type", _SM["glyph"] | _SM["defaultLibrary"]
                elif prev in (TT.COLON, TT.ARROW) and (text in KNOWN_TYPES or text in ix.structs) and \
                        (prev == TT.ARROW or _annotation_colon(ix, i)):
                    ttype = "type" if text in KNOWN_TYPES else "struct"
                    if text in KNOWN_TYPES:
                        mods |= _SM["defaultLibrary"]
                elif prev == TT.AT:
                    ttype = "decorator"
                else:
                    d = ix.resolve(i)
                    if prev == TT.DOT:
                        ttype = "method" if nxt == TT.LPAREN else "property"
                        if d is not None and d.kind == "field":
                            ttype = "property"
                    elif d is not None:
                        k = d.kind
                        ttype = {"function": "function", "method": "method", "struct": "struct", "trait": "type",
                                 "field": "property", "parameter": "parameter", "module": "namespace",
                                 "loopvar": "variable", "variable": "variable", "constant": "variable"}.get(k, "variable")
                        if k == "constant":
                            mods |= _SM["readonly"]
                        if d.line == t.sline and d.col == t.scol:
                            mods |= _SM["declaration"]
                        if k in ("function", "method"):
                            if d.name in native:
                                mods |= _SM["native"]
                            elif d.name in pyfn:
                                mods |= _SM["python"]
                        if d.bridge:
                            mods |= _SM["bridge"]
                    elif text in ix.structs:
                        ttype = "struct"
                    elif text in SYM_BUILTINS or text in PY_BUILTINS:
                        ttype, mods = "function" if nxt == TT.LPAREN else "variable", _SM["defaultLibrary"]
                    elif nxt == TT.LPAREN:
                        ttype = "function"
                    else:
                        ttype = "variable"
            elif t.type == TT.SELF:
                ttype = "keyword"
            if ttype is None:
                continue
            line = t.sline - 1
            lt = doc.lines[line] if line < len(doc.lines) else ""
            char = cp_to_u16(lt, t.scol - 1)
            length = cp_to_u16(lt, t.scol - 1 + (t.end - t.start)) - char
            if length <= 0 or "\n" in text:
                continue
            dl = line - prev_line
            dc = char - prev_char if dl == 0 else char
            data += [dl, dc, length, _ST[ttype], mods]
            prev_line, prev_char = line, char
        return {"data": data}

    # ── hover ──────────────────────────────────────────────────────────
    def _tok_at(self, doc, pos):
        ix = self.ix(doc)
        line = pos["line"] + 1
        lt = doc.lines[pos["line"]] if pos["line"] < len(doc.lines) else ""
        from lsp.text import u16_to_cp
        col = u16_to_cp(lt, pos["character"]) + 1
        return ix, ix.token_at(line, col)

    def m_textDocument_hover(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return self._foreign_hover(doc, p["position"])
        ix, i = self._tok_at(doc, p["position"])
        if i is None:
            return None
        t = ix.tokens[i]
        rng = doc.range(t.sline, t.scol, t.sline, t.scol + (t.end - t.start))
        text = doc.text[t.start:t.end]
        md = None
        if t.type in SPELLING and not (i and ix.tokens[i - 1].type == TT.DOT):
            n, c, s = SPELLING[t.type]
            md = f"**{text}** — {KEYWORD_DOCS.get(t.type, t.type.name.lower())}\n\n" \
                 f"| normal | compact | symbol |\n|:-:|:-:|:-:|\n| `{n}` | `{c}` | `{s}` |"
        elif t.type == TT.IDENT and text in GLYPH_TYPES:
            md = f"**{text}** — the `{GLYPH_TYPES[text]}` type (symbol mode)"
        elif t.type in (TT.IDENT, TT.SELF):
            d = ix.resolve(i)
            if d is not None:
                md = self._def_hover(doc, ix, d)
            elif t.value in SYM_BUILTINS:
                md = _md(SYM_BUILTINS[t.value]) + "\n\nSym runtime builtin"
            elif t.value in PY_BUILTINS:
                md = _md(f"{t.value}(...)", "python") + "\n\nPython builtin (Sym passes it through)"
            elif i > 1 and ix.tokens[i - 1].type == TT.DOT:
                owner = ix.tokens[i - 2]
                od = ix.resolve(i - 2) if owner.type == TT.IDENT else None
                if od is not None and od.bridge:
                    bridge, member = od.bridge, t.value

                    def work():
                        h = self._bridge_member_hover(bridge, member)
                        return {"contents": {"kind": "markdown", "value": h}, "range": rng} if h else None
                    return self.async_respond(self._current_id, work)
        elif t.type == TT.DIRECTIVE:
            md = f"**#{t.value}** — this file is written in **{t.value}** mode. " \
                 f"Convert it with *Sym: Convert file to…* (lossless, verified by re-parsing)."
        if md is None:
            return None
        return {"contents": {"kind": "markdown", "value": md}, "range": rng}

    def _def_hover(self, doc, ix, d):
        kw = SPELLING[TT.FN][MODES.index(ix.mode)]
        parts = []
        if d.kind in ("function", "method"):
            f = next((f for f in ix.functions if f.name == d.name and f.line == d.line), None)
            sig = f.signature(kw) if f else d.detail
            if d.container:
                sig = f"{d.container}.{sig[len(kw) + 1:]}" if sig.startswith(kw + " ") else sig
            parts.append(_md(sig))
            for e in self._purity_cache.get(doc.uri, []):
                if e["name"] == d.name:
                    if e["native"]:
                        parts.append("⚡ **Compiles to native C** — pure, typed, no Python dependencies.")
                    else:
                        where = f" (line {e['reasonLine'] + 1})" if e.get("reasonLine") is not None else ""
                        parts.append(f"◌ **Stays Python** — {e['reason']}{where}")
                    break
        elif d.kind in ("struct", "trait"):
            s = ix.structs.get(d.name)
            body = "\n".join(f"    {f.name}: {f.type or 'any'}" for f in (s.fields if s else []))
            meths = "\n".join(f"    fn {m}(…)" for m in (s.methods if s else []))
            parts.append(_md(f"{d.detail}\n{body}" + (f"\n\n{meths}" if meths else "")))
        elif d.kind == "module":
            parts.append(_md(d.detail))
            if d.bridge:
                parts.append(f"Lives in **{_lang_label(d.bridge[0])}** — `{d.bridge[1]}`. "
                             f"Members complete after `.` from the real runtime.")
        elif d.kind == "field":
            parts.append(_md(f"{d.container}.{d.name}: {d.type or 'any'}"))
        elif d.kind == "parameter":
            parts.append(_md(f"(parameter) {d.detail}"))
        else:
            label = {"loopvar": "loop variable", "constant": "constant"}.get(d.kind, "variable")
            parts.append(_md(f"({label}) {d.name}" + (f": {d.type}" if d.type else "")))
            if d.bridge and len(d.bridge) == 3:
                parts.append(f"Live **{_lang_label(d.bridge[0])}** object (`{d.bridge[1]}`) — Sym holds a handle; "
                             f"every method call routes back to where it lives.")
        if d.doc:
            parts.append(d.doc)
        return "\n\n".join(parts)

    def _bridge_member_hover(self, bridge, member):
        try:
            from bridge.introspect import describe
            desc = describe(bridge[0], bridge[1], cwd=self.root, timeout=8)
        except Exception:
            return None
        sigs = [m["signature"] for m in desc.get("members", []) if m["name"] == member]
        if not sigs:
            return None
        doc = next((m.get("detail") for m in desc["members"] if m["name"] == member and m.get("detail")), "")
        body = "\n".join(sigs[:8]) + (f"\n… {len(sigs) - 8} more overloads" if len(sigs) > 8 else "")
        return _md(body, _md_lang(bridge[0])) + f"\n\n{_lang_label(bridge[0])} · `{bridge[1]}`" + \
            (f"\n\n{doc}" if doc and doc != member else "")

    # ── completion ─────────────────────────────────────────────────────
    def m_textDocument_completion(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return self._foreign_completion(doc, p["position"], p.get("_id"))
        pos = p["position"]
        line_text = doc.lines[pos["line"]] if pos["line"] < len(doc.lines) else ""
        from lsp.text import u16_to_cp
        col = u16_to_cp(line_text, pos["character"])
        before = line_text[:col]
        ix = self.ix(doc)

        # inside an embedded block: leave it to that language
        off = doc.offset_at(pos)
        for r in ix.regions:
            if r["body_start"] <= off <= r["body_end"]:
                return {"isIncomplete": False, "items": []}

        # lang.import <target>
        m = re.match(r"^\s*(java|js|sym|python|ruby|php|perl|r)\.import\s+([\w.:]*)$", before)
        if m:
            return self._import_target_completion(m.group(1), m.group(2))
        m = re.match(r"^\s*(?:import|<<|⊂)\s+(?:py\.)?([\w.]*)$", before)
        if m:
            return self._import_target_completion("python", m.group(1))

        # member access
        m = re.search(r"([A-Za-z_][\w]*|\)|self)\s*\.\s*(\w*)$", before)
        if m:
            return self._member_completion(doc, ix, pos, m)
        if re.search(r"\.\s*$", before):
            return {"isIncomplete": False, "items": []}

        items = []
        mode = ix.mode
        midx = MODES.index(mode)
        seen = set()
        line1 = pos["line"] + 1
        for d in ix.visible_defs(line1):
            if d.name in seen:
                continue
            seen.add(d.name)
            item = {"label": d.name, "kind": KIND_TO_CK.get(d.kind, CK["variable"]),
                    "detail": d.detail or d.kind, "sortText": "0" + ("0" if d.scope is not None else "1") + d.name}
            if d.kind in ("function", "struct") or (d.kind == "module" and d.bridge and len(d.bridge) == 3):
                f = ix.function_by_name(d.name)
                if d.kind == "function" and f is not None:
                    ps = ", ".join(f"${{{k + 1}:{q.name}}}" for k, q in enumerate(f.params) if q.name != "self")
                    item.update(insertText=f"{d.name}({ps})", insertTextFormat=2)
                    item["labelDetails"] = {"detail": "(" + ", ".join(q.detail for q in f.params) + ")",
                                            "description": f.ret or ""}
            items.append(item)
        for name, sig in SYM_BUILTINS.items():
            if name not in seen:
                items.append({"label": name, "kind": CK["function"] if "(" in sig else CK["constant"],
                              "detail": sig, "sortText": "2" + name})
        for name in ("len", "range", "str", "int", "float", "list", "dict", "set", "abs", "min", "max", "sum",
                     "sorted", "enumerate", "zip", "map", "filter", "round", "input", "open", "isinstance", "type"):
            if name not in seen and name not in SYM_BUILTINS:
                items.append({"label": name, "kind": CK["function"], "detail": "Python builtin", "sortText": "3" + name})
        # keywords, spelled for this file's mode
        for tt, sp in SPELLING.items():
            word = sp[midx]
            if tt in (TT.ARROW, TT.LTE, TT.GTE, TT.NEQ, TT.ASSIGN, TT.FAT_ARROW, TT.PIPE):
                continue
            item = {"label": word, "kind": CK["keyword"], "sortText": "4" + sp[0],
                    "detail": f"{sp[0]}" + (f"  ·  {sp[1]}  ·  {sp[2]}" if len(set(sp)) > 1 else "")}
            if word != sp[0]:
                item["filterText"] = sp[0]  # type the normal word, get the glyph
            items.append(item)
        for label, body, desc in SNIPPETS.get(mode, []) + (SNIPPETS["normal"] if mode == "normal" else []):
            if any(it["label"] == label + " …" for it in items):
                continue
            items.append({"label": label + " …", "kind": CK["snippet"], "insertText": body, "insertTextFormat": 2,
                          "detail": desc, "sortText": "1" + label,
                          "filterText": {"ƒ": "fn", "∀": "for", "⟳": "while", "∏": "struct"}.get(label, label)})
        if before.strip() == "" or re.fullmatch(r"\s*\w*", before):
            for label, body, desc in BLOCK_SNIPPETS + BRIDGE_IMPORT_SNIPPETS:
                items.append({"label": label, "kind": CK["snippet"], "insertText": body, "insertTextFormat": 2,
                              "detail": desc, "sortText": "5" + label})
        # type names after ':' / '->'
        if re.search(r"(:|->|→)\s*\w*$", before):
            types = [("int", "ℤ"), ("float", "ℝ"), ("bool", "𝔹"), ("str", "𝕊"), ("list", "𝕃"), ("dict", "𝔻")]
            items = [{"label": g if mode == "symbol" else n, "kind": CK["typeparam"], "detail": n,
                      "filterText": n, "sortText": "0" + n} for n, g in types]
            items += [{"label": s, "kind": CK["struct"], "sortText": "1" + s} for s in ix.structs]
        return {"isIncomplete": False, "items": items}

    def _member_completion(self, doc, ix, pos, m):
        recv = m.group(1)
        # find the token index of the receiver
        from lsp.text import u16_to_cp
        line1 = pos["line"] + 1
        lt = doc.lines[pos["line"]]
        col = u16_to_cp(lt, pos["character"])
        before = lt[:col]
        recv_end_col = len(before) - len(m.group(0)) + len(recv)
        k = ix.token_at(line1, recv_end_col)
        items = []
        if k is not None:
            d = ix.resolve(k)
            if d is not None and d.bridge:
                lang, target = d.bridge[0], d.bridge[1]
                instance = len(d.bridge) == 3
                return self._bridge_items_async(lang, target, instance)
            owner = ix.receiver_type(k)
            if owner and owner in ix.structs:
                s = ix.structs[owner]
                for f in s.fields:
                    items.append({"label": f.name, "kind": CK["field"], "detail": f.type or "any", "sortText": "0" + f.name})
                for name in s.methods:
                    fn = next((f for f in ix.functions if f.name == name and f.container == owner), None)
                    ps = ", ".join(f"${{{j + 1}:{q.name}}}" for j, q in enumerate(fn.params[1:] if fn else []))
                    items.append({"label": name, "kind": CK["method"], "detail": fn.signature() if fn else "",
                                  "insertText": f"{name}({ps})", "insertTextFormat": 2, "sortText": "1" + name})
                return {"isIncomplete": False, "items": items}
            if owner == "str":
                return {"isIncomplete": False, "items": _py_type_members(str)}
            if owner == "list":
                return {"isIncomplete": False, "items": _py_type_members(list)}
            if owner == "dict":
                return {"isIncomplete": False, "items": _py_type_members(dict)}
        # unknown receiver: offer every member name used in this file
        seen = set()
        for i, t in enumerate(ix.tokens):
            if t.type == TT.IDENT and i and ix.tokens[i - 1].type == TT.DOT and t.value not in seen:
                seen.add(t.value)
                items.append({"label": t.value, "kind": CK["property"], "sortText": "9" + t.value})
        return {"isIncomplete": True, "items": items}

    def _bridge_items(self, lang, target, instance=False):
        from bridge.introspect import describe
        try:
            desc = describe(lang, target, cwd=self.root or os.getcwd())
        except Exception as e:
            return {"isIncomplete": False, "items": [{"label": "⚠ " + _lang_label(lang) + ": " + str(e).split("\n")[0][:120],
                                                      "kind": CK["text"], "insertText": ""}]}
        return {"isIncomplete": False, "items": _describe_items(desc, lang, instance)}

    def _bridge_items_async(self, lang, target, instance=False):
        mid = self._current_id
        return self.async_respond(mid, lambda: self._bridge_items(lang, target, instance))

    def _import_target_completion(self, lang, partial):
        mid = self._current_id
        real = "python" if lang in ("sym", "python") else lang

        def work():
            items = []
            if "." in partial or (real == "java" and partial.count(".") >= 1):
                parent = partial.rsplit(".", 1)[0] if "." in partial else ""
                if parent:
                    try:
                        from bridge.introspect import describe
                        desc = describe(real, parent, cwd=self.root or os.getcwd())
                        for mbr in desc.get("members", []):
                            if mbr["kind"] in ("class", "package", "module", "interface", "enum", "namespace", "function"):
                                items.append({"label": mbr["name"], "kind": KIND_TO_CK.get(mbr["kind"], CK["module"]),
                                              "detail": mbr.get("signature", "")})
                    except Exception:
                        pass
                return {"isIncomplete": True, "items": items}
            if real == "python":
                for name in self.python_modules():
                    items.append({"label": name, "kind": CK["module"], "detail": "python"})
            elif real == "java":
                for name in ("java", "javax", "org", "com", "net", "io"):
                    items.append({"label": name, "kind": CK["folder"], "detail": "java package root"})
                for name in _local_java_packages(self.root or os.getcwd()):
                    items.append({"label": name, "kind": CK["folder"], "detail": "local (./ or ./javalib)"})
            elif real == "js":
                for name in _node_modules(self.root or os.getcwd()):
                    items.append({"label": name, "kind": CK["module"], "detail": "node_modules"})
                for name in ("fs", "path", "os", "crypto", "url", "util", "events", "child_process", "zlib"):
                    items.append({"label": "node:" + name, "kind": CK["module"], "detail": "node builtin"})
            return {"isIncomplete": True, "items": items}
        return self.async_respond(mid, work)

    def python_modules(self):
        if self._py_modules is None:
            import subprocess
            try:
                out = subprocess.run([sys.executable, "-c",
                                      "import pkgutil,sys;print('\\n'.join(sorted({m.name for m in pkgutil.iter_modules()} | set(sys.builtin_module_names))))"],
                                     capture_output=True, text=True, timeout=20)
                self._py_modules = [m for m in out.stdout.split() if not m.startswith("_")]
            except Exception:
                self._py_modules = []
        return self._py_modules

    # ── foreign files: Sym bridge calls inside .py / .js / .rb / ... ──────
    _BIND = re.compile(r"(?:^|[\s;])(?:const\s+|let\s+|var\s+|my\s+|\$)?([A-Za-z_]\w*)\s*(?::=|=)\s*"
                       r"(?:await\s+)?\(?\s*(?:await\s+)?[Ss]ym(?:\.|::|->)"
                       r"(java\.package|java|js|python|ruby|php|perl|r|imp)\s*\(\s*[\"']([^\"']+)[\"']\s*\)\s*\)?"
                       r"(\s*\(\s*\)|\s*\.\s*new\s*\(\s*\))?")
    _CALLSTR = re.compile(r"[Ss]ym(?:\.|::|->)(?:call|Call|request|new_obj|newObj)\s*\(\s*(?:[\"'](\w+)[\"']|lang\s*:\s*[\"'](\w+)[\"'])"
                          r"\s*,\s*(?:target\s*:\s*)?[\"']([\w.:#$]*)$")

    def _foreign_context(self, doc, pos):
        from lsp.text import u16_to_cp
        lt = doc.lines[pos["line"]] if pos["line"] < len(doc.lines) else ""
        before = lt[:u16_to_cp(lt, pos["character"])]
        m = self._CALLSTR.search(before)
        if m:
            lang = m.group(1) or m.group(2)
            partial = m.group(3)
            parent = partial.rsplit(".", 1)[0] if "." in partial else ""
            return {"kind": "callstring", "lang": lang, "target": parent, "prefix": partial.rsplit(".", 1)[-1],
                    "partial": partial}
        m = re.search(r"([A-Za-z_]\w*)\s*(?:\.|->|::)\s*(\w*)$", before)
        if m:
            name = m.group(1)
            upto = "\n".join(doc.lines[:pos["line"]]) + "\n" + before
            bind = None
            for b in self._BIND.finditer(upto):
                if b.group(1) == name:
                    bind = b
            if bind:
                lang = bind.group(2)
                target = bind.group(3)
                inst = bool(bind.group(4))
                if lang == "java.package":
                    lang = "java"
                if lang == "imp":
                    lang = "python"
                return {"kind": "member", "lang": lang, "target": target, "instance": inst, "prefix": m.group(2),
                        "name": name}
            # chained navigation:  sym.java.util.ArrayList.  /  lucene.index.
            chain = re.search(r"[Ss]ym\.(java)\.((?:\w+\.)*\w+)\.(\w*)$", before)
            if chain:
                return {"kind": "member", "lang": "java", "target": chain.group(2), "instance": False,
                        "prefix": chain.group(3)}
        return None

    def _foreign_completion(self, doc, pos, mid=None):
        ctx = self._foreign_context(doc, pos)
        if ctx is None:
            return {"isIncomplete": False, "items": []}
        if ctx["kind"] == "callstring" and not ctx["target"]:
            if ctx["lang"] == "python":
                return {"isIncomplete": True, "items": [{"label": m, "kind": CK["module"]} for m in self.python_modules()]}
            return {"isIncomplete": True, "items": []}
        return self._bridge_items_async(ctx["lang"], ctx["target"], ctx.get("instance", False))

    def _foreign_hover(self, doc, pos):
        from lsp.text import u16_to_cp
        lt = doc.lines[pos["line"]] if pos["line"] < len(doc.lines) else ""
        col = u16_to_cp(lt, pos["character"])
        m = re.match(r"\w*", lt[col:])
        end = col + (len(m.group(0)) if m else 0)
        p2 = {"line": pos["line"], "character": cp_to_u16(lt, end)}
        ctx = self._foreign_context(doc, p2)
        if ctx is None:
            return None
        member = ctx["prefix"]
        if not member:
            return None
        target = ctx["target"] if ctx["kind"] == "member" else ctx["target"]
        mid = self._current_id

        def work():
            md = self._bridge_member_hover((ctx["lang"], target), member)
            return {"contents": {"kind": "markdown", "value": md}} if md else None
        return self.async_respond(mid, work)

    def m_sym_foreignCompletion(self, p):
        doc = Document(p.get("uri", "untitled:x"), p["text"], 0, p.get("languageId", "plaintext"))
        return self._foreign_completion(doc, p["position"])

    def m_sym_foreignHover(self, p):
        doc = Document(p.get("uri", "untitled:x"), p["text"], 0, p.get("languageId", "plaintext"))
        return self._foreign_hover(doc, p["position"])

    def m_sym_describe(self, p):
        mid = self._current_id

        def work():
            from bridge.introspect import describe
            return describe(p["lang"], p["target"], cwd=p.get("cwd") or self.root or os.getcwd())
        return self.async_respond(mid, work)

    # ── signature help ─────────────────────────────────────────────────
    def m_textDocument_signatureHelp(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return None
        ix = self.ix(doc)
        off = doc.offset_at(p["position"])
        # walk back to the unmatched '('
        depth, k, active = 0, off - 1, 0
        txt = doc.text
        while k >= 0:
            ch = txt[k]
            if ch in ")]}":
                depth += 1
            elif ch in "([{":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                active += 1
            elif ch == "\n" and depth == 0 and k < off - 1 and txt[k + 1:off].strip() == "":
                pass
            k -= 1
        if k < 0 or txt[k] != "(":
            return None
        m = re.search(r"([\w]+)\s*$", txt[max(0, k - 80):k])
        if not m:
            return None
        name = m.group(1)
        f = ix.function_by_name(name)
        if f is not None:
            params = [q for q in f.params if q.name != "self"]
            label = f.signature(SPELLING[TT.FN][MODES.index(ix.mode)])
            sig = {"label": label, "parameters": [{"label": q.detail} for q in params]}
            for e in self._purity_cache.get(doc.uri, []):
                if e["name"] == name:
                    sig["documentation"] = "⚡ native C" if e["native"] else f"◌ Python — {e['reason']}"
            return {"signatures": [sig], "activeSignature": 0, "activeParameter": min(active, max(0, len(params) - 1))}
        if name in ix.structs:
            s = ix.structs[name]
            label = f"{name}(" + ", ".join(f"{q.name}: {q.type or 'any'}" for q in s.fields) + ")"
            return {"signatures": [{"label": label, "parameters": [{"label": f"{q.name}: {q.type or 'any'}"} for q in s.fields]}],
                    "activeSignature": 0, "activeParameter": active}
        if name in SYM_BUILTINS and "(" in SYM_BUILTINS[name]:
            label = SYM_BUILTINS[name].split(" — ")[0]
            inner = label[label.index("(") + 1:label.rindex(")")] if ")" in label else ""
            params = [{"label": x.strip()} for x in inner.split(",") if x.strip()]
            return {"signatures": [{"label": label, "parameters": params}], "activeSignature": 0, "activeParameter": active}
        return None

    # ── navigation ─────────────────────────────────────────────────────
    def _def_location(self, doc, d):
        return {"uri": doc.uri, "range": doc.range(d.line, d.col, d.line, d.end_col)}

    def m_textDocument_definition(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return None
        ix, i = self._tok_at(doc, p["position"])
        if i is None:
            return None
        d = ix.resolve(i)
        if d is not None:
            if d.kind == "module" and not d.bridge or (d.bridge and d.bridge[0] == "python"):
                # sibling .sym module?
                path = os.path.join(os.path.dirname(uri_to_path(doc.uri)), (d.bridge[1] if d.bridge else d.name) + ".sym")
                if os.path.exists(path):
                    return {"uri": "file://" + path, "range": {"start": {"line": 0, "character": 0},
                                                              "end": {"line": 0, "character": 0}}}
            return self._def_location(doc, d)
        t = ix.tokens[i]
        if t.type == TT.IDENT and t.value in ix.structs:
            s = ix.structs[t.value]
            return {"uri": doc.uri, "range": doc.line_range(s.line)}
        # workspace: a function defined in another .sym file
        for uri, line, col, end in self.workspace_lookup(t.value):
            if uri != doc.uri:
                return {"uri": uri, "range": {"start": {"line": line, "character": col}, "end": {"line": line, "character": end}}}
        return None

    def m_textDocument_typeDefinition(self, p):
        doc = self.doc(p)
        ix, i = self._tok_at(doc, p["position"])
        if i is None:
            return None
        d = ix.resolve(i)
        if d is not None and d.type in ix.structs:
            s = ix.structs[d.type]
            return {"uri": doc.uri, "range": doc.line_range(s.line)}
        return None

    def _same_symbol(self, ix, i):
        d = ix.resolve(i)
        t = ix.tokens[i]
        name = t.value if t.type == TT.IDENT else None
        if name is None:
            return []
        out = []
        for k in ix.refs.get(name, []):
            if d is None:
                out.append(k)
            elif ix.resolve(k) is d:
                out.append(k)
        if d is not None and d.kind == "field":
            out = [k for k in ix.refs.get(name, []) if ix.resolve(k) is d or k in ix.ident_def and ix.ident_def[k] is d]
        return out

    def m_textDocument_references(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return None
        ix, i = self._tok_at(doc, p["position"])
        if i is None:
            return []
        out = []
        for k in self._same_symbol(ix, i):
            t = ix.tokens[k]
            out.append({"uri": doc.uri, "range": doc.range(t.sline, t.scol, t.sline, t.scol + (t.end - t.start))})
        return out

    def m_textDocument_documentHighlight(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return None
        ix, i = self._tok_at(doc, p["position"])
        if i is None:
            return []
        out = []
        for k in self._same_symbol(ix, i):
            t = ix.tokens[k]
            d = ix.ident_def.get(k)
            write = d is not None or (k + 1 < len(ix.tokens) and ix.tokens[k + 1].type in
                                      (TT.ASSIGN, TT.PLUS_EQ, TT.MINUS_EQ, TT.STAR_EQ, TT.SLASH_EQ))
            out.append({"range": doc.range(t.sline, t.scol, t.sline, t.scol + (t.end - t.start)), "kind": 3 if write else 2})
        return out

    # ── cross-language rename ─────────────────────────────────────────
    # `calc.add(…)` where calc came from `java.import math.Calculator` is a Java method. Renaming
    # it in Sym renames it everywhere Sym calls it AND tells the editor which source file defines
    # it; renaming the method in Calculator.java asks us for every Sym caller (sym/foreignReferences).
    def _foreign_member(self, ix, i):
        toks = ix.tokens
        if i is None or i < 2 or toks[i].type != TT.IDENT or toks[i - 1].type != TT.DOT or toks[i - 2].type != TT.IDENT:
            return None
        d = ix.resolve(i - 2)
        if d is None or not d.bridge:
            return None
        return {"lang": d.bridge[0], "target": d.bridge[1], "member": toks[i].value}

    @staticmethod
    def _owner_matches(target, owner):
        if not owner:
            return True
        last = re.split(r"[.:/]", target.rstrip("/"))[-1]
        last = re.sub(r"\.(js|mjs|cjs|ts|py|rb|php|pl)$", "", last)
        own = re.sub(r"\.(js|mjs|cjs|ts|py|rb|php|pl|java)$", "", re.split(r"[.:/]", owner)[-1] if "/" in owner else owner.split(".")[-1])
        return target == owner or last == own

    def foreign_references(self, lang, owner, member):
        """Every place a .sym file in the workspace uses `member` of a `lang` import (optionally only
        imports whose target is `owner`), plus whole-word uses inside that language's `lang> { }` blocks."""
        out = {}
        open_docs = {d.uri: d for d in self.docs.values()}
        paths = set(self.workspace_files())
        for d in open_docs.values():
            if d.uri.startswith("file://") and d.uri.endswith(".sym"):
                paths.add(d.uri[7:])
        word = re.compile(r"(?<![\w$])" + re.escape(member) + r"(?![\w$])")
        for path in sorted(paths):
            uri = "file://" + path
            doc = open_docs.get(uri)
            if doc is None:
                try:
                    with open(path, encoding="utf-8") as f:
                        doc = Document(uri, f.read())
                except OSError:
                    continue
            try:
                ix = Index(doc.text, path)
            except Exception:
                continue
            edits = []
            for k, t in enumerate(ix.tokens):
                if t.type != TT.IDENT or t.value != member:
                    continue
                fm = self._foreign_member(ix, k)
                if fm and fm["lang"] == lang and self._owner_matches(fm["target"], owner):
                    edits.append(doc.range(t.sline, t.scol, t.sline, t.scol + (t.end - t.start)))
            for r in ix.regions:
                if r["lang"] != lang:
                    continue
                body = doc.text[r["body_start"]:r["body_end"]]
                for m in word.finditer(body):
                    edits.append(doc.offset_range(r["body_start"] + m.start(), r["body_start"] + m.end()))
            if edits:
                out[uri] = edits
        return out

    def foreign_source(self, lang, target):
        """The workspace file that defines a foreign import, when it's the user's own code."""
        root = self.root or os.getcwd()
        exts = {"java": (".java",), "js": (".js", ".mjs", ".cjs", ".ts"), "python": (".py",), "ruby": (".rb",),
                "php": (".php",), "perl": (".pl", ".pm"), "go": (".go",), "rust": (".rs",)}.get(lang, ())
        if not exts:
            return None
        if lang in ("js", "ruby", "php", "perl") and ("/" in target or target.startswith(".")):
            for base in (root,):
                p = os.path.normpath(os.path.join(base, target))
                for cand in (p,) + tuple(p + e for e in exts):
                    if os.path.isfile(cand):
                        return cand
        last = target.split(".")[-1] if lang in ("java", "python") else re.split(r"[/:]", target)[-1]
        names = {last + e for e in exts}
        if lang == "python":
            names.add(target.replace(".", "/") + ".py")
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith(".") and d not in ("node_modules", "target", "__pycache__", "venv", ".venv", "dist", "build")]
            for f in fn:
                if f in names:
                    return os.path.join(dp, f)
        return None

    def m_sym_foreignReferences(self, p):
        refs = self.foreign_references(p["lang"], p.get("owner") or "", p["member"])
        return [{"uri": u, "range": r} for u, rs in refs.items() for r in rs]

    def m_sym_foreignMember(self, p):
        """What's under the cursor, if it's a foreign member: {lang, target, member, source}."""
        doc = self.doc(p)
        ix, i = self._tok_at(doc, p["position"])
        fm = self._foreign_member(ix, i)
        if fm:
            fm["source"] = self.foreign_source(fm["lang"], fm["target"])
        return fm

    def m_textDocument_prepareRename(self, p):
        doc = self.doc(p)
        ix, i = self._tok_at(doc, p["position"])
        if i is None or ix.tokens[i].type != TT.IDENT:
            raise LspError(-32602, "nothing to rename here")
        t = ix.tokens[i]
        if ix.resolve(i) is None and not self._foreign_member(ix, i):
            raise LspError(-32602, f"'{t.value}' is not defined in this file")
        return {"range": doc.range(t.sline, t.scol, t.sline, t.scol + (t.end - t.start)), "placeholder": t.value}

    def m_textDocument_rename(self, p):
        doc = self.doc(p)
        ix, i = self._tok_at(doc, p["position"])
        new = p["newName"]
        if not re.fullmatch(r"[A-Za-z_]\w*", new):
            raise LspError(-32602, f"'{new}' is not a valid identifier")
        kw = NORMAL_KEYWORDS if ix.mode == "normal" else COMPACT_KEYWORDS
        if new in kw:
            raise LspError(-32602, f"'{new}' is a keyword in {ix.mode} mode")
        if i is None:
            return None
        fm = self._foreign_member(ix, i)
        if fm:
            refs = self.foreign_references(fm["lang"], fm["target"], fm["member"])
            return {"changes": {u: [{"range": r, "newText": new} for r in rs] for u, rs in refs.items()}}
        edits = []
        for k in self._same_symbol(ix, i):
            t = ix.tokens[k]
            edits.append({"range": doc.range(t.sline, t.scol, t.sline, t.scol + (t.end - t.start)), "newText": new})
        return {"changes": {doc.uri: edits}}

    # ── symbols ────────────────────────────────────────────────────────
    def m_textDocument_documentSymbol(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return []
        ix = self.ix(doc)
        out = []
        natives = {e["name"]: e for e in self.purity(doc)}

        def full_range(l1, l2):
            l2 = max(l1, min(l2, len(doc.lines)))
            return {"start": {"line": l1 - 1, "character": 0},
                    "end": {"line": l2 - 1, "character": cp_to_u16(doc.lines[l2 - 1], len(doc.lines[l2 - 1]))}}

        for s in ix.structs.values():
            if s.kind == "impl":
                continue
            children = [{"name": f.name, "kind": SK["field"], "detail": f.type or "",
                         "range": doc.range(f.line, f.col, f.line, f.end_col),
                         "selectionRange": doc.range(f.line, f.col, f.line, f.end_col)} for f in s.fields]
            for f in ix.functions:
                if f.container == s.name:
                    children.append({"name": f.name, "kind": SK["method"], "detail": f.signature()[3:],
                                     "range": full_range(f.start_line, f.end_line),
                                     "selectionRange": doc.range(f.line, f.col, f.line, f.end_col)})
            d = next((d for d in ix.defs if d.name == s.name and d.kind in ("struct", "trait")), None)
            sel = doc.range(d.line, d.col, d.line, d.end_col) if d else doc.line_range(s.line)
            out.append({"name": s.name, "kind": SK["struct"] if s.kind == "struct" else SK["interface"],
                        "range": full_range(s.start_line, s.end_line), "selectionRange": sel, "children": children})
        for f in ix.functions:
            if f.container:
                continue
            e = natives.get(f.name)
            detail = f.signature()[3:]
            if e is not None:
                detail = ("⚡ " if e["native"] else "") + detail
            out.append({"name": f.name, "kind": SK["function"], "detail": detail,
                        "range": full_range(f.start_line, f.end_line),
                        "selectionRange": doc.range(f.line, f.col, f.line, f.end_col)})
        for d in ix.defs:
            if d.scope is None and d.kind in ("variable", "constant", "module"):
                out.append({"name": d.name, "kind": KIND_TO_SK.get(d.kind, SK["variable"]), "detail": d.type or d.detail,
                            "range": doc.range(d.line, d.col, d.line, d.end_col),
                            "selectionRange": doc.range(d.line, d.col, d.line, d.end_col)})
        for r in ix.regions:
            rng = doc.offset_range(r["start"], r["end"])
            out.append({"name": f"{r['lang']}> {{ }}", "kind": SK["namespace"], "detail": f"{_lang_label(r['lang'])} block",
                        "range": rng, "selectionRange": {"start": rng["start"], "end": rng["start"]}})
        out.sort(key=lambda s: (s["range"]["start"]["line"], s["range"]["start"]["character"]))
        return out

    def workspace_files(self):
        root = self.root or os.getcwd()
        out = []
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith(".") and d not in ("node_modules", "target", "__pycache__",
                                                                         "venv", ".venv", "dist", "build")]
            for f in fn:
                if f.endswith(".sym"):
                    out.append(os.path.join(dp, f))
            if len(out) > 2000:
                break
        return out

    def workspace_lookup(self, name):
        if self._ws_index is None:
            idx = {}
            for path in self.workspace_files():
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except OSError:
                    continue
                uri = "file://" + path
                try:
                    ix = Index(text, path)
                except Exception:
                    continue
                doc = Document(uri, text)
                for d in ix.defs:
                    if d.scope is None and d.kind in ("function", "struct", "trait", "constant", "variable"):
                        r = doc.range(d.line, d.col, d.line, d.end_col)
                        idx.setdefault(d.name, []).append((uri, r["start"]["line"], r["start"]["character"],
                                                           r["end"]["character"], d.kind, d.detail))
            self._ws_index = idx
        return [(u, l, c, e) for (u, l, c, e, _, _) in self._ws_index.get(name, [])]

    def m_workspace_symbol(self, p):
        q = (p.get("query") or "").lower()
        self.workspace_lookup("")
        out = []
        for name, entries in self._ws_index.items():
            if q and not _fuzzy(q, name.lower()):
                continue
            for (u, l, c, e, kind, detail) in entries:
                out.append({"name": name, "kind": KIND_TO_SK.get(kind, SK["variable"]), "containerName": os.path.basename(u),
                            "location": {"uri": u, "range": {"start": {"line": l, "character": c},
                                                             "end": {"line": l, "character": e}}}})
            if len(out) > 500:
                break
        return out

    # ── code lens / inlay hints ────────────────────────────────────────
    def m_textDocument_codeLens(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return []
        out = []
        for e in self.purity(doc):
            title = "⚡ native C" if e["native"] else f"◌ Python · {e['reason']}"
            out.append({"range": e["range"], "command": {"title": title, "command": "sym.showEmit",
                                                         "arguments": [doc.uri, e["name"], "c" if e["native"] else "python"]}})
        return out

    def m_textDocument_inlayHint(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return []
        ix = self.ix(doc)
        r = p.get("range") or {"start": {"line": 0}, "end": {"line": len(doc.lines)}}
        lo, hi = r["start"]["line"] + 1, r["end"]["line"] + 1
        out = []
        type_word = (lambda t: TYPE_GLYPHS.get(t, t)) if ix.mode == "symbol" else (lambda t: t)
        for i, t in enumerate(ix.tokens):
            if not (lo <= t.sline <= hi):
                continue
            d = ix.ident_def.get(i)
            if d is not None and d.kind in ("variable", "constant") and d.type and \
                    i + 1 < len(ix.tokens) and ix.tokens[i + 1].type != TT.COLON:
                out.append({"position": doc.pos(d.line, d.end_col), "label": f": {type_word(d.type)}",
                            "kind": 1, "paddingLeft": False})
            # parameter names at call sites for literal arguments
            if t.type == TT.IDENT and i + 1 < len(ix.tokens) and ix.tokens[i + 1].type == TT.LPAREN and \
                    (i == 0 or ix.tokens[i - 1].type not in (TT.FN, TT.DOT)):
                f = ix.function_by_name(t.value)
                if f is None or ix.ident_def.get(i) is not None:
                    continue
                params = [q for q in f.params if q.name != "self"]
                j, argn, depth, arg_start = i + 2, 0, 0, i + 2
                while j < len(ix.tokens) and argn < len(params):
                    tj = ix.tokens[j]
                    if tj.type in (TT.LPAREN, TT.LBRACKET, TT.LBRACE):
                        depth += 1
                    elif tj.type in (TT.RPAREN, TT.RBRACKET, TT.RBRACE):
                        if depth == 0:
                            break
                        depth -= 1
                    if depth == 0 and tj.type == TT.COMMA:
                        argn += 1
                        arg_start = j + 1
                    elif j == arg_start and tj.type in (TT.INTEGER, TT.FLOAT, TT.STRING, TT.TRUE, TT.FALSE, TT.NONE) \
                            and ix.tokens[j + 1].type in (TT.COMMA, TT.RPAREN) and len(params) > 1:
                        out.append({"position": doc.pos(tj.sline, tj.scol), "label": f"{params[argn].name}:",
                                    "kind": 2, "paddingRight": True})
                    j += 1
        return out

    # ── code actions ───────────────────────────────────────────────────
    def m_textDocument_codeAction(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return []
        ix = self.ix(doc)
        out = []
        for diag in (p.get("context") or {}).get("diagnostics", []):
            if diag.get("code") == "mode-keyword" and (diag.get("data") or {}).get("replace"):
                rep = diag["data"]["replace"]
                out.append({"title": f"Write '{rep}'", "kind": "quickfix", "diagnostics": [diag], "isPreferred": True,
                            "edit": {"changes": {doc.uri: [{"range": diag["range"], "newText": rep}]}}})
            if diag.get("code") == "unused":
                pass
        for target in MODES:
            if target == ix.mode:
                continue
            try:
                new = morph(doc.text, target)
            except MorphError as e:
                out.append({"title": f"Convert file to {target} mode", "kind": "refactor.rewrite",
                            "disabled": {"reason": str(e)}})
                continue
            out.append({"title": f"Convert file to {target} mode", "kind": "refactor.rewrite",
                        "edit": {"changes": {doc.uri: [_full_edit(doc, new)]}}})
        return out

    # ── formatting / folding ───────────────────────────────────────────
    def m_textDocument_formatting(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return []
        opts = p.get("options") or {}
        size = int(opts.get("tabSize", 4))
        ix = self.ix(doc)
        out_lines = []
        in_region = _region_lines(doc, ix)
        blank = 0
        for n, line in enumerate(doc.lines):
            if n in in_region:
                out_lines.append(line.rstrip())
                blank = 0
                continue
            stripped = line.rstrip()
            lead = len(stripped) - len(stripped.lstrip(" \t"))
            indent = stripped[:lead].replace("\t", " " * size)
            stripped = indent + stripped[lead:]
            if stripped == "":
                blank += 1
                if blank > 2:
                    continue
            else:
                blank = 0
            out_lines.append(stripped)
        while out_lines and out_lines[-1] == "":
            out_lines.pop()
        new = "\n".join(out_lines) + "\n"
        if new == doc.text:
            return []
        return [_full_edit(doc, new)]

    def m_textDocument_foldingRange(self, p):
        doc = self.doc(p)
        if not self.is_sym(doc):
            return []
        ix = self.ix(doc)
        out = []
        for f in ix.functions:
            if f.end_line > f.start_line:
                out.append({"startLine": f.start_line - 1, "endLine": f.end_line - 1, "kind": "region"})
        for s in ix.structs.values():
            if s.end_line > s.start_line:
                out.append({"startLine": s.start_line - 1, "endLine": s.end_line - 1})
        for r in ix.regions:
            a, b = doc.position_at(r["start"])["line"], doc.position_at(r["end"])["line"]
            if b > a:
                out.append({"startLine": a, "endLine": b, "kind": "region"})
        # comment blocks
        start = None
        for n, l in enumerate(doc.lines + [""]):
            if l.strip().startswith("#") and l.strip() not in ("#compact", "#symbol"):
                start = n if start is None else start
            else:
                if start is not None and n - 1 > start:
                    out.append({"startLine": start, "endLine": n - 1, "kind": "comment"})
                start = None
        return out


_ASYNC = object()


def _full_edit(doc, new):
    last = len(doc.lines) - 1
    return {"range": {"start": {"line": 0, "character": 0},
                      "end": {"line": last, "character": cp_to_u16(doc.lines[last], len(doc.lines[last]))}},
            "newText": new}


def _region_lines(doc, ix):
    s = set()
    for r in ix.regions:
        a, b = doc.position_at(r["body_start"])["line"], doc.position_at(r["body_end"])["line"]
        s.update(range(a, b))
    return s


def _annotation_colon(ix, i):
    """Is the ':' before token i a type annotation (vs a dict literal / slice)?"""
    k = i - 2
    if k < 0:
        return True
    t = ix.tokens[k]
    if t.type != TT.IDENT and t.type != TT.SELF:
        return False
    before = ix.tokens[k - 1].type if k else None
    return before in (TT.LET, TT.CONST, TT.LPAREN, TT.COMMA, TT.INDENT, TT.NEWLINE, None) or before is None


def _in_type_brackets(ix, i):
    k = i - 1
    depth = 0
    while k >= 0:
        t = ix.tokens[k].type
        if t == TT.RBRACKET:
            depth += 1
        elif t == TT.LBRACKET:
            if depth == 0:
                return k > 0 and ix.tokens[k - 1].type == TT.IDENT and ix.tokens[k - 1].value in KNOWN_TYPES
            depth -= 1
        elif t in (TT.NEWLINE, TT.ASSIGN):
            return False
        k -= 1
    return False


def _lang_label(lang):
    return {"java": "Java", "js": "JavaScript", "python": "Python", "ruby": "Ruby", "php": "PHP", "perl": "Perl",
            "r": "R", "go": "Go", "rust": "Rust", "c": "C", "sym": "Sym", "ui": "UI", "css": "CSS",
            "html": "HTML", "frontend": "Frontend"}.get(lang, lang)


def _md_lang(lang):
    return {"js": "javascript", "r": "r"}.get(lang, lang)


def _describe_items(desc, lang, instance):
    items = []
    by_name = {}
    for m in desc.get("members", []):
        if instance and (m["kind"] == "constructor" or (m.get("static") and m["kind"] in ("method", "field"))):
            continue
        if not instance and m["kind"] == "method" and m.get("static") is False and desc.get("kind") == "class" \
                and lang == "java":
            # instance methods on a class reference: still list, after statics
            pass
        by_name.setdefault(m["name"], []).append(m)
    for name, ms in by_name.items():
        m = ms[0]
        kind = KIND_TO_CK.get(m["kind"], CK["property"])
        detail = m.get("signature", "")
        if len(ms) > 1:
            detail += f"  (+{len(ms) - 1} overload{'s' if len(ms) > 2 else ''})"
        item = {"label": name, "kind": kind, "detail": detail,
                "sortText": ("0" if m.get("static") != instance else "1") + name,
                "labelDetails": {"description": _lang_label(lang)}}
        if m.get("detail") and m["detail"] != name:
            item["documentation"] = {"kind": "markdown", "value": m["detail"]}
        if m["kind"] in ("method", "function", "constructor") and "(" in detail:
            item["insertText"] = name + "(${1})"
            item["insertTextFormat"] = 2
            item["command"] = {"title": "signature", "command": "editor.action.triggerParameterHints"}
        items.append(item)
    return items


def _py_type_members(tp):
    out = []
    for n in dir(tp):
        if n.startswith("_"):
            continue
        out.append({"label": n, "kind": CK["method"], "detail": f"{tp.__name__}.{n}", "insertText": n + "(${1})",
                    "insertTextFormat": 2})
    return out


def _node_modules(root):
    out = []
    nm = os.path.join(root, "node_modules")
    if os.path.isdir(nm):
        for n in sorted(os.listdir(nm)):
            if n.startswith("."):
                continue
            if n.startswith("@"):
                for m in sorted(os.listdir(os.path.join(nm, n))):
                    out.append(f"{n}/{m}")
            else:
                out.append(n)
    for d in ("jslib", "."):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            for n in os.listdir(p):
                if n.endswith((".mjs", ".js")) and d == "jslib":
                    out.append(n.rsplit(".", 1)[0])
    return out


def _local_java_packages(root):
    out = set()
    for base in (root, os.path.join(root, "javalib")):
        if os.path.isdir(base):
            for n in os.listdir(base):
                p = os.path.join(base, n)
                if os.path.isdir(p) and not n.startswith(".") and any(
                        f.endswith((".java", ".class")) for _, _, fs in os.walk(p) for f in fs):
                    out.add(n)
    return sorted(out)


def _fuzzy(q, s):
    i = 0
    for ch in s:
        if i < len(q) and ch == q[i]:
            i += 1
    return i == len(q)


def main(argv=None):
    import io
    # stdout carries the protocol: anything else printing there would corrupt it
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    server = Server(Transport(sys.stdin.buffer, out))
    orig = server.dispatch

    def dispatch(msg):
        server._current_id = msg.get("id")
        if msg.get("method") == "textDocument/completion":
            msg.setdefault("params", {})["_id"] = msg.get("id")
        return orig(msg)
    server.dispatch = dispatch
    server.run()


if __name__ == "__main__":
    main()

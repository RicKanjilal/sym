"""
sym lsp — end-to-end tests. Spawns `sym lsp` and speaks real LSP to it.

    python3 tests/test_lsp.py
"""

import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Client:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "bin", "sym.py"), "lsp"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
        self.id = 0
        self.responses = {}
        self.notes = []
        self.cv = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=lambda: [None for _ in self.p.stderr], daemon=True).start()

    def _reader(self):
        f = self.p.stdout
        while True:
            headers = {}
            while True:
                line = f.readline()
                if not line:
                    return
                line = line.decode().strip()
                if not line:
                    break
                k, v = line.split(":", 1)
                headers[k.lower()] = v.strip()
            body = json.loads(f.read(int(headers["content-length"])).decode())
            with self.cv:
                if "id" in body and "method" not in body:
                    self.responses[body["id"]] = body
                else:
                    self.notes.append(body)
                self.cv.notify_all()

    def send(self, msg):
        data = json.dumps(msg).encode()
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
        self.p.stdin.flush()

    def request(self, method, params, timeout=60):
        self.id += 1
        mid = self.id
        self.send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        end = time.time() + timeout
        with self.cv:
            while mid not in self.responses:
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError(method)
                self.cv.wait(left)
            r = self.responses.pop(mid)
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
        return r["result"]

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def wait_note(self, method, uri, timeout=20):
        end = time.time() + timeout
        with self.cv:
            while True:
                for n in reversed(self.notes):
                    if n.get("method") == method and n["params"].get("uri") == uri:
                        return n["params"]
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError(method)
                self.cv.wait(left)

    def close(self):
        try:
            self.request("shutdown", None, timeout=5)
            self.notify("exit", None)
        except Exception:
            pass
        self.p.kill()


SRC = '''# Geometry helpers
struct Point
    x: float
    y: float

    fn norm(self) -> float
        return sqrt(self.x * self.x + self.y * self.y)

# The classic.
fn fib(n: int) -> int
    if n <= 1
        return n
    return fib(n - 1) + fib(n - 2)

fn label(n: int) -> str
    let unused = 3
    return "n = {n}"

java.import java.util.ArrayList as AL
let p = Point(3.0, 4.0)
let total = fib(20)
print p.norm()
print missing_name
let xs = AL()
xs.add("hi")
'''

SYMBOL_SRC = '''#symbol
ƒ sq(n: ℤ) → ℤ
    ← n * n

∂ 𝕊name ≔ 1
⊕ sq(4)
'''

passed = failed = 0


def check(name, cond, info=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}  {info}")


def main():
    c = Client()
    init = c.request("initialize", {"rootUri": "file://" + ROOT, "capabilities": {}})
    caps = init["capabilities"]
    check("initialize advertises semantic tokens + inlay hints", "semanticTokensProvider" in caps and caps.get("inlayHintProvider"))
    c.notify("initialized", {})
    uri = "file:///tmp/velnor_test.sym"
    c.notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "sym", "version": 1, "text": SRC}})
    diags = c.wait_note("textDocument/publishDiagnostics", uri)["diagnostics"]
    msgs = [d["message"] for d in diags]
    check("undefined name reported", any("missing_name" in m for m in msgs), msgs)
    check("no false 'undefined' for builtins/struct/bridge", not any(n in " ".join(msgs) for n in ("'sqrt'", "'Point'", "'AL'", "'xs'")), msgs)
    check("unused local is a faded hint", any(d.get("code") == "unused" and d.get("tags") == [1] for d in diags))
    pur = c.wait_note("sym/purityChanged", uri)["functions"]
    by = {f["name"]: f for f in pur}
    check("purity: fib compiles to native C", by.get("fib", {}).get("native") is True, by.get("fib"))
    check("purity: label stays Python with a reason", by.get("label", {}).get("native") is False and by["label"]["reason"], by.get("label"))

    # hover on fib (line 10, col 4)
    h = c.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 9, "character": 4}})
    hv = h["contents"]["value"] if h else ""
    check("hover shows signature + native verdict + doc comment",
          "fn fib(n: int) -> int" in hv and "native C" in hv and "The classic." in hv, hv)
    h = c.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 9, "character": 0}})
    check("hover on keyword shows all three spellings", h and "ƒ" in h["contents"]["value"], h)

    # completion: struct members after p.
    lines = SRC.split("\n")
    src2 = SRC + "p.\n"
    c.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                        "contentChanges": [{"text": src2}]})
    comp = c.request("textDocument/completion", {"textDocument": {"uri": uri}, "position": {"line": len(lines) - 1, "character": 2}})
    labels = [i["label"] for i in comp["items"]]
    check("member completion on struct instance (fields + methods)", {"x", "y", "norm"} <= set(labels), labels[:20])

    # bridge completion: xs. → live Java ArrayList instance methods
    src3 = SRC + "xs.\n"
    c.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 3},
                                        "contentChanges": [{"text": src3}]})
    comp = c.request("textDocument/completion", {"textDocument": {"uri": uri}, "position": {"line": len(lines) - 1, "character": 3}},
                     timeout=120)
    labels = [i["label"] for i in comp["items"]]
    check("cross-runtime completion: Java ArrayList methods on a live handle", {"add", "size", "stream"} <= set(labels), labels[:15])

    # general completion includes defs + keywords + snippets
    c.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 4}, "contentChanges": [{"text": SRC}]})
    comp = c.request("textDocument/completion", {"textDocument": {"uri": uri}, "position": {"line": 21, "character": 0}})
    labels = [i["label"] for i in comp["items"]]
    check("completion has functions, structs, builtins, keywords",
          all(x in labels for x in ("fib", "Point", "sqrt", "while")), labels[:30])

    # definition: fib( call on line 21 → def on line 10
    d = c.request("textDocument/definition", {"textDocument": {"uri": uri}, "position": {"line": 20, "character": 13}})
    check("go to definition", d and d["range"]["start"]["line"] == 9, d)
    refs = c.request("textDocument/references", {"textDocument": {"uri": uri}, "position": {"line": 9, "character": 4}})
    check("references (def + 2 recursive calls + 1 use)", len(refs) == 4, len(refs))
    ren = c.request("textDocument/rename", {"textDocument": {"uri": uri}, "position": {"line": 9, "character": 4}, "newName": "fibo"})
    check("rename edits every occurrence", len(ren["changes"][uri]) == 4)
    try:
        c.request("textDocument/rename", {"textDocument": {"uri": uri}, "position": {"line": 9, "character": 4}, "newName": "while"})
        check("rename refuses keywords", False)
    except RuntimeError:
        check("rename refuses keywords", True)

    syms = c.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    names = [s["name"] for s in syms]
    check("document symbols: struct with children, functions, top-level lets",
          "Point" in names and "fib" in names and any(s["name"] == "Point" and s.get("children") for s in syms), names)

    st = c.request("textDocument/semanticTokens/full", {"textDocument": {"uri": uri}})
    check("semantic tokens produced", len(st["data"]) > 50 and len(st["data"]) % 5 == 0)
    native_bit = 1 << 3
    check("semantic tokens carry the 'native' modifier", any((st["data"][k + 4] & native_bit) for k in range(0, len(st["data"]), 5)))

    lens = c.request("textDocument/codeLens", {"textDocument": {"uri": uri}})
    check("code lenses for purity", any("native C" in l["command"]["title"] for l in lens), [l["command"]["title"] for l in lens])
    hints = c.request("textDocument/inlayHint", {"textDocument": {"uri": uri}, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 40, "character": 0}}})
    check("inlay hints: inferred let types", any(h["label"] == ": int" for h in hints), [h["label"] for h in hints])

    acts = c.request("textDocument/codeAction", {"textDocument": {"uri": uri}, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
                                                "context": {"diagnostics": []}})
    titles = [a["title"] for a in acts if not a.get("disabled")]
    check("code actions: convert to compact / symbol", "Convert file to symbol mode" in titles, titles)

    sig = c.request("textDocument/signatureHelp", {"textDocument": {"uri": uri}, "position": {"line": 20, "character": 16}})
    check("signature help", sig and sig["signatures"][0]["label"].startswith("fn fib("), sig)

    em = c.request("sym/emit", {"textDocument": {"uri": uri}, "target": "c"})
    check("emit C is function-linked", "fib" in em["code"] and any(l["name"] == "fib" for l in em["links"]), em["links"])
    em = c.request("sym/emit", {"textDocument": {"uri": uri}, "target": "python"})
    check("emit Python", "def label" in em["code"])

    gt = c.request("sym/glyphTable", {})
    check("glyph table served", any(r["normal"] == "fn" and r["symbol"] == "ƒ" for r in gt["tokens"]))
    mo = c.request("sym/morph", {"textDocument": {"uri": uri}, "to": "symbol"})
    check("morph to symbol", mo["text"].startswith("#symbol") and "ƒ fib(n: ℤ) → ℤ" in mo["text"])

    # symbol-mode doc with astral glyphs: UTF-16 positions must line up
    suri = "file:///tmp/velnor_sym.sym"
    c.notify("textDocument/didOpen", {"textDocument": {"uri": suri, "languageId": "sym", "version": 1, "text": SYMBOL_SRC}})
    c.wait_note("textDocument/publishDiagnostics", suri)
    st = c.request("textDocument/semanticTokens/full", {"textDocument": {"uri": suri}})
    # decode absolute positions
    toks, line, ch = [], 0, 0
    data = st["data"]
    for k in range(0, len(data), 5):
        line += data[k]
        ch = data[k + 1] if data[k] else ch + data[k + 1]
        toks.append((line, ch, data[k + 2]))
    # line 2 "    ← n * n"  → '←' at char 4 length 1
    check("UTF-16: BMP glyph positions", (2, 4, 1) in toks, toks)
    h = c.request("textDocument/hover", {"textDocument": {"uri": suri}, "position": {"line": 1, "character": 8}})
    check("hover on ℤ in symbol mode", h and "int" in h["contents"]["value"], h)

    # regions
    puri = "file:///tmp/velnor_poly.sym"
    poly = 'let n = 2\njs> {\n  sym.x = [1,2].map(v => v * 2);\n}\nprint n\n'
    c.notify("textDocument/didOpen", {"textDocument": {"uri": puri, "languageId": "sym", "version": 1, "text": poly}})
    regs = c.request("sym/regions", {"textDocument": {"uri": puri}})
    check("embedded js block region", len(regs) == 1 and regs[0]["lang"] == "js" and regs[0]["bodyRange"]["start"]["line"] == 1, regs)
    pd = c.wait_note("textDocument/publishDiagnostics", puri)["diagnostics"]
    check("no diagnostics leak from inside a js> block", not pd, pd)
    iuri = "file:///tmp/velnor_imports.sym"
    imp = 'java.import math.Calculator\njava.import java.lang.Math as JMath\njs.import textkit\nlet a = Calculator.add(5, 8)\nlet p = JMath.pow(2, 3)\nprint textkit.shout("x")\n'
    c.notify("textDocument/didOpen", {"textDocument": {"uri": iuri, "languageId": "sym", "version": 1, "text": imp}})
    idg = c.wait_note("textDocument/publishDiagnostics", iuri)["diagnostics"]
    check("bridge imports produce no false 'undefined' warnings", not idg, idg)

    # foreign completion: a Python file using Sym
    py = 'import sym\nlst = sym.java("java.util.ArrayList")()\nlst.'
    fc = c.request("sym/foreignCompletion", {"languageId": "python", "text": py, "position": {"line": 2, "character": 4}}, timeout=120)
    labels = [i["label"] for i in fc["items"]]
    check("foreign completion: Java methods inside a .py file", "add" in labels and "size" in labels, labels[:10])
    py = 'import sym\nm = sym.call("java", "java.util.Collections.'
    fc = c.request("sym/foreignCompletion", {"languageId": "python", "text": py, "position": {"line": 1, "character": len(py.split(chr(10))[1])}}, timeout=120)
    labels = [i["label"] for i in fc["items"]]
    check("foreign completion inside sym.call(\"java\", \"…\") strings", "sort" in labels and "emptyList" in labels, labels[:10])

    fmt = c.request("textDocument/formatting", {"textDocument": {"uri": puri}, "options": {"tabSize": 4, "insertSpaces": True}})
    check("formatting returns edits or nothing", isinstance(fmt, list))
    fold = c.request("textDocument/foldingRange", {"textDocument": {"uri": uri}})
    check("folding ranges for functions and structs", len(fold) >= 3, fold)

    # broken file: lexer/parse errors must not kill the outline
    buri = "file:///tmp/velnor_broken.sym"
    broken = 'fn ok(n: int) -> int\n    return n\n\nfn bad(\n    return "unterminated\n\nfn after() -> int\n    return 1\n'
    c.notify("textDocument/didOpen", {"textDocument": {"uri": buri, "languageId": "sym", "version": 1, "text": broken}})
    bd = c.wait_note("textDocument/publishDiagnostics", buri)["diagnostics"]
    check("syntax errors reported", any(d["severity"] == 1 for d in bd), bd)
    syms = c.request("textDocument/documentSymbol", {"textDocument": {"uri": buri}})
    check("outline survives a broken file", {"ok", "after"} <= {s["name"] for s in syms}, [s["name"] for s in syms])

    # compact file using a normal keyword → quick fix
    curi = "file:///tmp/velnor_compact.sym"
    c.notify("textDocument/didOpen", {"textDocument": {"uri": curi, "languageId": "sym", "version": 1,
                                                       "text": "#compact\n@ x = 1\nreturn x\n"}})
    cd = c.wait_note("textDocument/publishDiagnostics", curi)["diagnostics"]
    mk = [d for d in cd if d.get("code") == "mode-keyword"]
    check("compact file: 'return' flagged with the compact spelling", mk and "<-" in mk[0]["message"], cd)
    acts = c.request("textDocument/codeAction", {"textDocument": {"uri": curi}, "range": mk[0]["range"] if mk else {},
                                                "context": {"diagnostics": mk}}) if mk else []
    check("quick fix rewrites the keyword", any(a["title"] == "Write '<-'" for a in acts), [a["title"] for a in acts])

    # cross-language rename: a Java method used from two Sym files
    a_uri, b_uri = "file:///tmp/velnor_xa.sym", "file:///tmp/velnor_xb.sym"
    c.notify("textDocument/didOpen", {"textDocument": {"uri": a_uri, "languageId": "sym", "version": 1,
             "text": "java.import math.Calculator\nlet calc = Calculator()\nprint calc.add(1, 2)\nlet add = 3\n"}})
    c.notify("textDocument/didOpen", {"textDocument": {"uri": b_uri, "languageId": "sym", "version": 1,
             "text": "java.import math.Calculator as C\nprint C.add(4, 5)\njava> {\n  int r = new Calculator().add(1, 1);\n}\n"}})
    fm = c.request("sym/foreignMember", {"textDocument": {"uri": a_uri}, "position": {"line": 2, "character": 12}})
    check("foreign member under the cursor", fm and fm["lang"] == "java" and fm["member"] == "add", fm)
    ren = c.request("textDocument/rename", {"textDocument": {"uri": a_uri}, "position": {"line": 2, "character": 12}, "newName": "plus"})
    ch = (ren or {}).get("changes", {})
    check("rename reaches every Sym caller (not the Sym variable `add`)",
          len(ch.get(a_uri, [])) == 1 and len(ch.get(b_uri, [])) == 2, ch)
    refs = c.request("sym/foreignReferences", {"lang": "java", "owner": "Calculator", "member": "add"})
    check("Java side can ask for Sym callers", len([r for r in refs if r["uri"] in (a_uri, b_uri)]) == 3, refs)

    c.close()
    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

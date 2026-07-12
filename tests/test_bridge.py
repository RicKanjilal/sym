"""SymBridge tests — the polyglot host must never silently break.
Run:  python3 -m pytest tests/test_bridge.py -v   (or just python3 tests/test_bridge.py)
Requires: node on PATH; JDK (javac) for Java tests — Java tests skip on JRE-only.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from bridge.broker import get_bridge, BridgeError, _to_symbol, _from_symbol

HAS_NODE = shutil.which("node") is not None
HAS_JDK = shutil.which("javac") is not None or shutil.which("java") is not None


def test_symbol_object_roundtrip():
    cases = [None, True, 42, 3.14, "hi", [1, [2, 3]], {"a": {"b": [1, "x"]}}]
    for c in cases:
        assert _from_symbol(_to_symbol(c)) == c
    assert _from_symbol(_to_symbol(b"\x00\xff")) == b"\x00\xff"


def test_js_call():
    if not HAS_NODE: return
    b = get_bridge()
    assert b.call("js", "Math.max", [3, 9, 2]) == 9
    assert b.call("js", "JSON.stringify", [{"a": 1}]) == '{"a":1}'


def test_js_exec_shared():
    if not HAS_NODE: return
    b = get_bridge()
    b.shared["nums"] = [1, 2, 3]
    b.exec_block("js", "sym.total = sym.nums.reduce((a, x) => a + x, 0);")
    assert b.shared["total"] == 6


def test_java_stdlib_call():
    if not HAS_JDK: return
    b = get_bridge()
    assert b.call("java", "java.lang.Integer.parseInt", ["7"]) == 7
    assert b.call("java", "java.lang.Math.pow", [2, 8]) == 256.0


def test_java_source_autocompile():
    if not HAS_JDK: return
    os.chdir(os.path.join(ROOT, "examples"))
    b = get_bridge()
    b.close()  # worker cwd is fixed at launch — relaunch in examples/
    mod = b.import_module("java", "math.Calculator")
    assert mod.add(5, 8) == 13
    assert mod.fib(10) == 55


def test_java_exec_shared():
    if not HAS_JDK: return
    b = get_bridge()
    b.shared["n"] = 21
    b.exec_block("java",
        'long n = ((Number) sym.get("n")).longValue(); sym.put("twice", n * 2);')
    assert b.shared["twice"] == 42


def test_error_surfaces_cleanly():
    if not HAS_NODE: return
    b = get_bridge()
    try:
        b.call("js", "no.such.function", [])
        assert False, "should have raised"
    except BridgeError as e:
        assert "no.such.function" in str(e) or "not" in str(e)


def test_sym_run_polyglot_demo():
    """End-to-end: the full compiler pipeline runs the demo."""
    if not (HAS_NODE and HAS_JDK): return
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "bin", "sym.py"), "run", "polyglot_demo.sym"],
        cwd=os.path.join(ROOT, "examples"), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "= 13" in r.stdout          # Java add
    assert "12586269025" in r.stdout   # Java fib(50)
    assert "SYMBOLCORE IS THE HOST" in r.stdout  # JS
    assert "216" in r.stdout           # Java summed JS output




# ── the full polyglot suite ──────────────────────────────────
def _has(cmd):
    import shutil as _sh
    return _sh.which(cmd) is not None


def test_php_worker():
    if not _has("php"): return
    b = get_bridge()
    assert b.call("php", "strtoupper", ["sym"]) == "SYM"
    b.shared["n"] = 5
    b.exec_block("php", '$sym["fact"] = array_product(range(1, $sym["n"])); return null;')
    assert b.shared["fact"] == 120


def test_ruby_worker():
    if not _has("ruby"): return
    b = get_bridge()
    assert b.call("ruby", "Math.sqrt", [144]) == 12.0
    b.exec_block("ruby", 'sym["rev"] = "abc".reverse')
    assert b.shared["rev"] == "cba"


def test_perl_worker():
    if not _has("perl"): return
    b = get_bridge()
    b.exec_block("perl", '$sym{j} = join("-", 1, 2, 3); 1;')
    assert b.shared["j"] == "1-2-3"


def test_r_worker():
    if not _has("Rscript"): return
    b = get_bridge()
    assert b.call("r", "mean", [[1, 2, 3, 4, 100]]) == 22
    b.shared["xs"] = [2.0, 4.0, 6.0, 8.0]
    b.exec_block("r", "sym$sd <- sd(unlist(sym$xs))")
    assert abs(b.shared["sd"] - 2.582) < 0.01


def test_go_block():
    if not _has("go"): return
    b = get_bridge()
    b.shared["nums"] = [3.0, 4.0]
    b.exec_block("go", 'xs := symNums("nums")\nsymSet("hyp", xs[0]*xs[0]+xs[1]*xs[1])')
    assert b.shared["hyp"] == 25


def test_rust_block():
    if not _has("rustc"): return
    b = get_bridge()
    b.shared["nums"] = [2.0, 3.0, 7.0]
    b.exec_block("rust",
        'let p: f64 = sym.get("nums").as_f64s().iter().product();\n'
        'sym.set("prod", J::num(p));')
    assert b.shared["prod"] == 42


def test_c_ffi():
    from bridge.extras import c_import
    m = c_import("m")
    assert abs(m.call("sqrt", [2.0], "double") - 1.41421) < 0.001
    try:
        sq = c_import("sqlite3")
        assert sq.call("sqlite3_libversion", [], "str").startswith("3.")
    except RuntimeError:
        pass  # sqlite not present on this machine — fine


def test_registry_routes():
    from bridge.extras import sym_import, _registry
    assert _registry().get("numpy") == "python"
    assert _registry().get("lodash") == "js"
    assert _registry().get("ggplot2") == "r"
    math_mod = sym_import("math", lambda: get_bridge())  # python stdlib probe
    assert math_mod.sqrt(9) == 3.0


def test_ultimate_demo():
    if not all(_has(c) for c in ("node", "javac", "php", "ruby", "Rscript", "go", "rustc")):
        return
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "bin", "sym.py"), "run", "polyglot_ultimate.sym"],
        cwd=os.path.join(ROOT, "examples"), capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    for needle in ("25.0", "19683", "42.0", "216", "11416", "84",
                   "'js', 'php', 'ruby', 'r', 'java'"):
        assert needle in r.stdout, f"missing {needle}"





# ── handles, overloads, clients ──────────────────────────────
def test_java_handles():
    if not _has("javac"): return
    b = get_bridge()
    AL = b.import_module("java", "java.util.ArrayList")
    lst = AL()                       # bare call constructs
    lst.add("a"); lst.add("b")
    assert lst.size() == 2
    l2 = AL()
    l2.addAll(lst)                   # handle as argument
    assert l2.size() == 2
    SB = b.import_module("java", "java.lang.StringBuilder")
    sb = SB("x")
    sb.append("-"); sb.append(7)     # overload: append(String) vs append(int)
    assert sb.toString() == "x-7"


def test_js_handles():
    if not _has("node"): return
    b = get_bridge()
    mod = b.import_module("js", "node:url")
    u = mod.URL.new("https://sym.dev/path?q=1")
    assert u.toString() == "https://sym.dev/path?q=1"


def test_python_client():
    import subprocess as sp
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import sym\n"
        "assert sym.call('java', 'java.lang.Math.pow', [2, 5]) == 32.0\n"
        "lst = sym.java('java.util.ArrayList')()\n"
        "lst.add('ok')\n"
        "assert lst.size() == 1\n"
        "sym.close(); print('CLIENT_OK')\n"
    ) % os.path.join(ROOT, "clients", "python")
    r = sp.run([sys.executable, "-c", code], capture_output=True, text=True,
               timeout=120, cwd=os.path.join(ROOT, "examples"),
               env={**os.environ, "SYM_HOME": ROOT})
    assert "CLIENT_OK" in r.stdout, r.stderr


def test_node_client():
    if not _has("node"): return
    import subprocess as sp
    script = os.path.join(ROOT, "examples", "test_node_client.mjs")
    if not os.path.exists(script): return
    r = sp.run(["node", script], capture_output=True, text=True, timeout=120,
               cwd=os.path.join(ROOT, "examples"),
               env={**os.environ, "SYM_HOME": ROOT})
    assert "NODE CLIENT ALIVE" in r.stdout, r.stderr


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  ❌ {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

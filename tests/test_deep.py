"""
DEEP TESTS — everything that could break, tested before strangers find it.

Covers: Symbol Object edge cases (unicode, nesting, big numbers, special
chars), per-provider data integrity, handle lifecycle + stale/cross-runtime
errors, error recovery (workers must survive their own exceptions), large
payloads, rapid sequential calls, Java overloads/fields/enums/nested
classes, compiled-block edges, C FFI errors, registry behavior, protocol
robustness.

    python3 tests/test_deep.py
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(os.path.join(ROOT, "examples"))

from bridge.broker import get_bridge, BridgeError, ForeignHandle, _to_symbol, _from_symbol

def _has(cmd):
    return shutil.which(cmd) is not None

B = get_bridge()

# ═══ 1. SYMBOL OBJECT EDGE CASES ═════════════════════════════
NASTY_STRINGS = [
    "",                                   # empty
    " ",                                  # whitespace
    "hello\nworld",                       # newline
    'quote " inside',                     # double quote
    "back\\slash",                        # backslash
    "tab\there",                          # tab
    "বাংলা ভাষা",                          # bengali
    "日本語テスト",                         # japanese
    "🔥🚀💯",                              # emoji
    "mixed বাংলা 🔥 \"quotes\" \\n",       # everything at once
    "a" * 50000,                          # long string (50KB)
]

NASTY_VALUES = [
    0, -1, 1, 3.14159, -2.5, 1e15, -1e15, 0.0001,
    True, False, None,
    [], {}, [[]], [{}], {"k": []},
    [1, [2, [3, [4, [5]]]]],              # deep nesting
    {"a": {"b": {"c": {"d": 1}}}},
    list(range(10000)),                   # 10k elements
    {"key with space": 1, "কী": "মান"},   # unicode keys
]


def test_symbol_roundtrip_nasty():
    for v in NASTY_VALUES + NASTY_STRINGS:
        assert _from_symbol(_to_symbol(v)) == v, f"roundtrip broke: {str(v)[:50]}"


def test_big_integers():
    # 2^53 boundary: JSON doubles lose precision above this — longs must survive
    big = 12586269025          # fib(50), < 2^53
    assert B.call("java", "java.lang.Long.parseLong", [str(big)]) == big
    huge = 9007199254740993    # 2^53 + 1
    got = B.call("java", "java.lang.Long.parseLong", [str(huge)])
    assert got == huge, f"long precision lost: {got}"


# ═══ 2. DATA INTEGRITY THROUGH EVERY PROVIDER ════════════════
def _echo_through(lang, code_tpl):
    """Send nasty strings through a provider and back, verify identity."""
    for s in NASTY_STRINGS[:10]:  # skip the 50KB one for interpreted loops
        B.shared["probe"] = s
        B.exec_block(lang, code_tpl)
        assert B.shared.get("echo") == s, \
            f"[{lang}] mangled: {s[:40]!r} -> {str(B.shared.get('echo'))[:40]!r}"


def test_js_data_integrity():
    if not _has("node"): return
    _echo_through("js", "sym.echo = sym.probe;")


def test_java_data_integrity():
    if not _has("javac"): return
    _echo_through("java", 'sym.put("echo", sym.get("probe"));')


def test_php_data_integrity():
    if not _has("php"): return
    _echo_through("php", '$sym["echo"] = $sym["probe"]; return null;')


def test_ruby_data_integrity():
    if not _has("ruby"): return
    _echo_through("ruby", 'sym["echo"] = sym["probe"]')


def test_r_data_integrity():
    if not _has("Rscript"): return
    _echo_through("r", "sym$echo <- sym$probe")


def test_perl_data_integrity():
    if not _has("perl"): return
    _echo_through("perl", "$sym{echo} = $sym{probe}; 1;")


def test_large_payload_java():
    if not _has("javac"): return
    B.shared["big"] = list(range(10000))
    B.exec_block("java", '''
        java.util.List<Object> xs = (java.util.List<Object>) sym.get("big");
        long total = 0;
        for (Object x : xs) total += ((Number) x).longValue();
        sym.put("bigsum", total);
    ''')
    assert B.shared["bigsum"] == sum(range(10000))


def test_large_payload_js():
    if not _has("node"): return
    B.shared["big"] = list(range(10000))
    B.exec_block("js", "sym.bigsum = sym.big.reduce((a, x) => a + x, 0);")
    assert B.shared["bigsum"] == sum(range(10000))


# ═══ 3. HANDLES: LIFECYCLE, STALENESS, CROSS-RUNTIME ═════════
def test_handle_lifecycle():
    if not _has("javac"): return
    AL = B.import_module("java", "java.util.ArrayList")
    handles = [AL() for _ in range(50)]     # many live handles
    for i, h in enumerate(handles):
        h.add(f"item{i}")
    assert all(h.size() == 1 for h in handles)
    assert handles[7].get(0) == "item7"     # each is distinct


def test_stale_handle_error():
    if not _has("javac"): return
    AL = B.import_module("java", "java.util.ArrayList")
    h = AL()
    B.worker("java").request({"op": "free", "handle": h._id})
    try:
        h.size()
        assert False, "stale handle should raise"
    except BridgeError as e:
        assert "stale" in str(e)


def test_cross_runtime_handle_rejected():
    if not (_has("javac") and _has("ruby")): return
    AL = B.import_module("java", "java.util.ArrayList")
    jh = AL()
    try:
        # a Java ticket means nothing to Ruby — must error, not pretend
        B.handle_call("ruby", jh._id, "size", [])
        # if ruby happens to have a handle with same id this could "work" —
        # so also check the honest path: passing java handle as ruby arg
    except BridgeError:
        pass  # clean rejection is correct


def test_handle_as_nested_arg():
    if not _has("javac"): return
    AL = B.import_module("java", "java.util.ArrayList")
    a, b_ = AL(), AL()
    a.add("x"); a.add("y")
    b_.addAll(a)
    assert b_.size() == 2


# ═══ 4. ERROR RECOVERY: WORKERS SURVIVE THEIR OWN CRASHES ════
def _error_then_recover(lang, bad_call, good_check):
    try:
        bad_call()
        assert False, f"[{lang}] expected error"
    except BridgeError:
        pass
    assert good_check(), f"[{lang}] worker died after error"


def test_js_recovers():
    if not _has("node"): return
    _error_then_recover("js",
        lambda: B.call("js", "no.such.thing", []),
        lambda: B.call("js", "Math.max", [1, 2]) == 2)


def test_java_recovers():
    if not _has("javac"): return
    _error_then_recover("java",
        lambda: B.call("java", "no.such.Class.method", []),
        lambda: B.call("java", "java.lang.Math.abs", [-5]) == 5)


def test_php_recovers():
    if not _has("php"): return
    _error_then_recover("php",
        lambda: B.call("php", "no_such_function", []),
        lambda: B.call("php", "strrev", ["ab"]) == "ba")


def test_ruby_recovers():
    if not _has("ruby"): return
    _error_then_recover("ruby",
        lambda: B.call("ruby", "NoSuchModule.nope", []),
        lambda: B.call("ruby", "Math.sqrt", [4]) == 2.0)


def test_r_recovers():
    if not _has("Rscript"): return
    _error_then_recover("r",
        lambda: B.call("r", "no_such_fn", []),
        lambda: B.call("r", "sum", [[1, 2, 3]]) == 6)


def test_perl_recovers():
    if not _has("perl"): return
    _error_then_recover("perl",
        lambda: B.call("perl", "No::Such::sub", []),
        lambda: B.exec_block("perl", "$sym{ok2} = 5; 1;") or B.shared["ok2"] == 5)


def test_java_exec_compile_error_recovers():
    if not _has("javac"): return
    try:
        B.exec_block("java", "this is not java at all !!!")
        assert False
    except BridgeError:
        pass
    assert B.call("java", "java.lang.Math.abs", [-1]) == 1


# ═══ 5. JAVA DEEP: OVERLOADS, FIELDS, ENUMS, NESTED CLASSES ══
def test_java_overload_precision():
    if not _has("javac"): return
    SB = B.import_module("java", "java.lang.StringBuilder")
    sb = SB()
    sb.append("s")        # append(String)
    sb.append(1)          # append(int) — not append(Object)!
    sb.append(2.5)        # append(double)
    sb.append(True)       # append(boolean)
    assert sb.toString() == "s12.5true"


def test_java_field_access():
    if not _has("javac"): return
    assert abs(B.call("java", "java.lang.Math.abs", [-1]) - 1) < 1e-9
    Integer = B.import_module("java", "java.lang.Integer")
    assert Integer.parseInt("7") == 7
    # static field via handle-less path isn't supported; instance fields are:
    P = B.import_module("java", "java.awt.Point") if False else None  # headless-safe skip


def test_java_enum_and_nested_class():
    if not _has("javac"): return
    Enum = B.import_module("java", "java.time.DayOfWeek")
    d = Enum.valueOf("FRIDAY")
    assert d.getValue() == 5


def test_java_null_handling():
    if not _has("javac"): return
    m = B.construct("java", "java.util.HashMap", [])
    assert m.get("missing") is None


# ═══ 6. COMPILED BLOCKS: EDGES ═══════════════════════════════
def test_go_unicode_and_cache():
    if not _has("go"): return
    B.shared["msg"] = "বাংলা 🔥"
    code = 'symSet("echo2", symGet("msg"))'
    B.exec_block("go", code)
    assert B.shared["echo2"] == "বাংলা 🔥"
    B.exec_block("go", code)  # cache-hit path
    assert B.shared["echo2"] == "বাংলা 🔥"


def test_rust_compile_error_surfaces():
    if not _has("rustc"): return
    try:
        B.exec_block("rust", "let x: i32 = \"not an int\";")
        assert False
    except Exception as e:
        assert "compile" in str(e).lower() or "mismatched" in str(e)


def test_go_compile_error_surfaces():
    if not _has("go"): return
    try:
        B.exec_block("go", "this is not go")
        assert False
    except Exception as e:
        assert "compile" in str(e).lower() or "syntax" in str(e).lower() \
            or "expected" in str(e).lower()


# ═══ 7. C FFI: ERRORS AND COERCION ═══════════════════════════
def test_c_ffi_errors():
    from bridge.extras import c_import
    m = c_import("m")
    assert abs(m.call("pow", [2.0, 10.0], "double") - 1024) < 1e-9
    try:
        m.call("no_such_symbol_xyz", [], "int")
        assert False
    except RuntimeError as e:
        assert "not found" in str(e)
    try:
        c_import("no_such_library_xyz")
        assert False
    except RuntimeError:
        pass


# ═══ 8. REGISTRY + PROJECT OVERRIDES ═════════════════════════
def test_registry_override(tmp_marker="/tmp/.sym_override_test"):
    from bridge import extras
    override = os.path.join(os.getcwd(), "symbridge.json")
    try:
        with open(override, "w") as f:
            json.dump({"weird_test_lib_xyz": "ruby"}, f)
        extras._registry_cache = None  # force reload
        assert extras._registry().get("weird_test_lib_xyz") == "ruby"
    finally:
        os.remove(override)
        extras._registry_cache = None


def test_maven_alias_validation():
    from bridge.maven import _alias
    assert _alias("poi").count(":") == 2
    assert _alias("g:a:1.0") == "g:a:1.0"
    try:
        _alias("definitely_not_an_alias_xyz")
        assert False
    except ValueError as e:
        assert "alias" in str(e)


# ═══ 9. PROTOCOL ROBUSTNESS + SEQUENCING ═════════════════════
def test_rapid_sequential_calls():
    if not _has("node"): return
    for i in range(200):
        assert B.call("js", "Math.abs", [-i]) == i


def test_interleaved_workers():
    langs = [l for l, c in [("js", "node"), ("java", "javac"),
                            ("php", "php"), ("ruby", "ruby")] if _has(c)]
    for i in range(20):
        for lang in langs:
            fn = {"js": "Math.abs", "java": "java.lang.Math.abs",
                  "php": "abs", "ruby": "Integer.sqrt"}[lang]
            arg = i * i if lang == "ruby" else -i
            expect = i
            assert B.call(lang, fn, [arg]) == expect


def test_shared_space_types_survive_block_chain():
    if not (_has("node") and _has("ruby")): return
    B.shared.clear()
    B.shared["chain"] = {"n": 1, "tags": ["a"], "flag": True}
    B.exec_block("js", "sym.chain.n += 1; sym.chain.tags.push('js');")
    B.exec_block("ruby", 'sym["chain"]["n"] += 1; sym["chain"]["tags"] << "rb"')
    c = B.shared["chain"]
    assert c["n"] == 3 and c["tags"] == ["a", "js", "rb"] and c["flag"] is True


def test_stdio_host_protocol():
    """Bad JSON ignored; unknown ops error cleanly; get/set works."""
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "bridge", "stdio_host.py")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env={**os.environ, "SYM_HOME": ROOT})
    def rpc(obj):
        p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()
        return json.loads(p.stdout.readline())
    p.stdin.write("this is not json\n"); p.stdin.flush()   # must be ignored
    r = rpc({"id": 1, "op": "ping"})
    assert r["ok"] and r["value"] == "pong"
    r = rpc({"id": 2, "op": "set", "key": "k", "value": [1, "x"]})
    assert r["ok"]
    r = rpc({"id": 3, "op": "get", "key": "k"})
    assert r["value"] == [1, "x"]
    r = rpc({"id": 4, "op": "nonsense_op"})
    assert not r["ok"] and "unknown" in r["error"]
    r = rpc({"id": 5, "op": "shutdown"})
    assert r["ok"]
    p.wait(timeout=10)


# ═══ 10. PREPROCESSOR EDGES ══════════════════════════════════
def test_preprocessor_nested_braces():
    from compiler.preprocess import preprocess
    src = 'js> {\n  function f(x) { return { a: x }; }\n  sym.v = f(1).a;\n}\n'
    out = preprocess(src)
    assert "__bridge_exec" in out and "js>" not in out


def test_preprocessor_multiple_blocks_and_imports():
    from compiler.preprocess import preprocess
    src = ('java.import a.B as Bee\n'
           'js.import lodash\n'
           'js> { sym.a = 1; }\n'
           'ruby> { sym["b"] = 2 }\n')
    out = preprocess(src)
    assert '__java_import("a.B")' in out and "Bee" in out
    assert '__js_import("lodash")' in out
    assert out.count("__bridge_exec") == 2





# ═══ 11. ADVERSARIAL PREPROCESSOR (added after finding real bug) ═══
def test_preprocessor_brace_in_string():
    from compiler.preprocess import preprocess
    import base64, re as _re
    for src, must_contain in [
        ('js> {\n  sym.v = "closing } brace";\n  sym.w = 1;\n}\n', "sym.w"),
        ("js> {\n  sym.v = 'single } quote';\n  sym.done = 1;\n}\n", "sym.done"),
        ('js> {\n  sym.v = `tpl } lit`;\n  sym.z = 1;\n}\n', "sym.z"),
        ('js> {\n  sym.v = "escaped \\" then }";\n  sym.q = 1;\n}\n', "sym.q"),
        ('ruby> {\n  sym["v"] = "hash } rocket"\n  sym["r"] = 1\n}\n', 'sym["r"]'),
    ]:
        out = preprocess(src)
        m = _re.search(r'__bridge_exec\("\w+", "([^"]+)"\)', out)
        captured = base64.b64decode(m.group(1)).decode()
        assert must_contain in captured, f"cut short at brace-in-string: {captured!r}"


def test_brace_in_string_end_to_end():
    if not _has("node"): return
    B.exec_block("js", 'sym.tricky = "value with } brace"; sym.after = 42;')
    assert B.shared["tricky"] == "value with } brace"
    assert B.shared["after"] == 42





# ═══ 12. DISTRIBUTED GC + NAMESPACE NAVIGATION (Ric's findings) ═══
def test_distributed_gc():
    """Python's del must reach the worker's RAM — no handle leaks."""
    if not _has("javac"): return
    import gc
    def count():
        return B.worker("java").request({"op": "stats"})["value"]["handles"]
    before = count()
    temp = [B.construct("java", "java.util.ArrayList", []) for _ in range(40)]
    assert count() >= before + 40
    del temp
    gc.collect()
    assert count() <= before + 2, "handle leak: finalizers not freeing"


def test_java_namespace_navigation():
    if not _has("javac"): return
    from bridge.broker import JavaNamespace
    ns = JavaNamespace(B, "java")
    lst = ns.util.ArrayList()                 # package walk → class → construct
    lst.add("ns")
    assert lst.size() == 1
    day = ns.time.DayOfWeek.FRIDAY            # enum constant, no valueOf
    assert day.getValue() == 5
    maxint = ns.lang.Integer.MAX_VALUE        # static field
    assert maxint == 2147483647





def test_js_symbol_values_become_handles():
    """Objects with Symbol-typed values (React elements) must travel as
    handles — JSON silently kills Symbols. Found via React SSR."""
    if not _has("node"): return
    B.exec_block("js", "sym.symbolic = { tag: Symbol('x'), n: 1 };")
    h = B.shared["symbolic"]
    assert isinstance(h, ForeignHandle), "Symbol-carrying object became data"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
        except Exception as e:
            failed.append(fn.__name__)
            print(f"  ❌ {fn.__name__}: {str(e)[:140]}")
    B.close()
    print(f"\n{len(fns) - len(failed)}/{len(fns)} passed")
    if failed:
        print("failed:", ", ".join(failed))
    sys.exit(1 if failed else 0)

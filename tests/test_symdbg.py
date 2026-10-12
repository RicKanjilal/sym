"""symdbg: breakpoints and stepping in Sym lines, js> blocks through V8's inspector,
live values (trace) and per-function timing (profile)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYM = [sys.executable, os.path.join(ROOT, "bin", "sym.py")]

DEMO = """fn square(n: int) -> int
    let r = n * n
    return r

let total = 0
for i in 0..4
    total += square(i)
print "total " + str(total)
js> {
    let z = 40 + 2;
    console.log("js says", z);
}
print "done"
"""


def session(src, flag, script, bps):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "demo.sym")
    open(p, "w").write(src)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(30)
    env = {**os.environ, "SYMDBG_ADDR": "127.0.0.1:%d" % srv.getsockname()[1], "SYMDBG_OUT": os.path.join(d, "out.json")}
    proc = subprocess.Popen(SYM + ["run", p] + flag.split(), env=env, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    c, _ = srv.accept()
    f = c.makefile("r")
    events = []
    script = list(script)

    def send(o):
        c.sendall((json.dumps(o) + "\n").encode())
    for line in f:
        ev = json.loads(line)
        events.append(ev)
        if ev["event"] == "ready":
            send({"cmd": "setBreakpoints", "file": p, "lines": bps})
            send({"cmd": "configurationDone"})
        elif ev["event"] == "stopped":
            send({"cmd": "variables", "frame": ev["frames"][0]["id"]})
            send({"cmd": script.pop(0) if script else "continue"})
        elif ev["event"] == "exited":
            break
    out = proc.communicate(timeout=30)[0]
    res = None
    if os.path.exists(os.path.join(d, "out.json")):
        res = json.load(open(os.path.join(d, "out.json")))
    shutil.rmtree(d, ignore_errors=True)
    return events, out, res


def stops(events):
    return [(e["frames"][0]["line"], e["frames"][0]["lang"]) for e in events if e["event"] == "stopped"]


def test_breakpoint_and_step_in_sym_lines():
    ev, out, _ = session(DEMO, "--debug", ["continue", "next"], [2])
    s = stops(ev)
    assert s[:3] == [(2, "sym"), (2, "sym"), (3, "sym")]
    v = [e for e in ev if e["event"] == "variables"][1]
    assert {"name": "n", "value": "1", "type": "int"} in v["scopes"][0]["items"]
    assert "total 14" in out


def test_step_into_js_block_and_back():
    if not shutil.which("node"):
        return
    ev, out, _ = session(DEMO, "--debug", ["stepIn", "next", "next"], [9])
    s = stops(ev)
    assert s == [(9, "sym"), (10, "js"), (11, "js"), (13, "sym")]
    js_stop = [e for e in ev if e["event"] == "stopped"][2]
    assert [f["lang"] for f in js_stop["frames"]] == ["js", "sym"]
    v = [e for e in ev if e["event"] == "variables"][2]
    assert {"name": "z", "value": "42", "type": "number"} in v["scopes"][0]["items"]
    assert "Debugger listening" not in out


def test_trace_records_values():
    _, out, res = session(DEMO, "--trace", [], [])
    lines = list(res["files"].values())[0]
    assert lines["7"]["text"] == "total = 14"
    assert lines["2"]["hits"] == 4
    assert lines["3"]["hist"][-1] == "← 9"


def test_profile_finds_the_hot_function():
    src = "fn slow(n: int) -> int\n    let t = 0\n    for i in 0..n\n        t += i % 7\n    return t\n\nprint slow(3000000)\n"
    _, out, res = session(src, "--profile --python", [], [])
    (key, st), = [(k, v) for k, v in res["functions"].items() if k.endswith("::slow")]
    assert st["line"] == 1 and st["share"] > 0.5, res


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for t in tests:
        try:
            t()
            ok += 1
            print("  ✓", t.__name__)
        except Exception as e:
            print("  ✗", t.__name__, repr(e))
    print(f"\n{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)

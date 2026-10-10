#!/usr/bin/env python3
"""Language additions found while building symreel in Sym:
`x not in y`, try/finally, raise with a value, default args on native functions,
non-scalar params staying Python, program arguments for `sym run`, recursive modules."""
import os, subprocess, sys, tempfile, textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYM = [sys.executable, os.path.join(ROOT, "bin", "sym.py")]
passed = failed = 0


def run(src, *args, files=None):
    d = tempfile.mkdtemp()
    for name, body in (files or {}).items():
        open(os.path.join(d, name), "w").write(textwrap.dedent(body))
    p = os.path.join(d, "main.sym")
    open(p, "w").write(textwrap.dedent(src))
    r = subprocess.run(SYM + ["run", p, *args], capture_output=True, text=True, timeout=120)
    return r.stdout + r.stderr


def check(name, src, want, *args, files=None):
    global passed, failed
    out = run(src, *args, files=files)
    if all(w in out for w in ([want] if isinstance(want, str) else want)):
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}\n{textwrap.indent(out, '      ')}")


check("not in", """
    let d = {"a": 1}
    if "z" not in d
        print "absent"
    """, "absent")

check("try / catch / finally", """
    fn f(x: int) -> str
        try
            if x > 0
                return "pos"
            let y = 1 / x
        catch e
            print "caught"
        finally
            print "cleanup {x}"
        return "end"
    print f(1)
    print f(0)
    """, ["cleanup 1", "pos", "caught", "cleanup 0", "end"])

check("try / finally without catch doesn't swallow", """
    fn g()
        try
            let z = 1 / 0
        finally
            print "fin"
    try
        g()
    catch e
        print "escaped: {e}"
    """, ["fin", "escaped: division by zero"])

check("raise keeps its message", """
    fn f(x: int) -> int
        if x < 0
            raise ValueError("negative: " + str(x))
        return x
    try
        f(-1)
    catch e
        print "caught: {e}"
    """, "caught: negative: -1")

check("bare raise re-raises", """
    try
        try
            let a = [1][5]
        catch e
            raise
    catch e2
        print "outer got it"
    """, "outer got it")

check("default args survive native compilation", """
    fn opt(a: int, b: int = 5) -> int
        return a + b
    print opt(1)
    print opt(1, b=2)
    """, ["6", "3"])

check("list-of-struct params stay Python", """
    struct Clip
        dur: float
    fn total(clips: list) -> float
        let t = 0.0
        for c in clips
            t = t + c.dur
        return t
    print total([Clip(1.5), Clip(2.25)])
    """, "3.75")

check("program arguments", """
    import py.sys as sys
    print sys.argv[1:]
    """, "['render', 'ep1', '--fast']", "render", "ep1", "--fast")

check("sym flags before --", """
    import py.sys as sys
    print sys.argv[1:]
    """, "['-x']", "--", "-x")

check("modules import modules", """
    import a
    print a.twice(21)
    """, "42", files={"a.sym": """
    import b
    fn twice(x: int) -> int
        return b.add(x, x)
    """, "b.sym": """
    fn add(x: int, y: int) -> int
        return x + y
    """})

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)

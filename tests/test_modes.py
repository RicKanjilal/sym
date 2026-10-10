"""
Modes — normal / compact / symbol are three spellings of one token stream.

Every example is converted to every mode and back. Each conversion must either
produce a file whose AST is identical to the original, or refuse with a reason.
Symbol-mode programs must run and print exactly what the normal-mode ones do.

    python3 tests/test_modes.py
"""

import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from compiler.modes import morph, MorphError, detect_mode, parse_source, ast_signature, glyph_table  # noqa
from compiler.lexer import Lexer, TT  # noqa

passed = failed = 0


def check(name, cond, info=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}  {info}")


files = sorted(glob.glob(os.path.join(ROOT, "examples", "*.sym"))) + [os.path.join(ROOT, "showcase", "sym_app.sym")]
for f in files:
    src = open(f, encoding="utf-8").read()
    base = ast_signature(parse_source(src).statements)
    for tgt in ("normal", "compact", "symbol"):
        name = f"{os.path.basename(f)} → {tgt}"
        try:
            out = morph(src, tgt)
        except MorphError as e:
            check(name + " (refused with reason)", "rename" in str(e) or "meaning" in str(e), str(e))
            continue
        same = ast_signature(parse_source(out).statements) == base
        back = morph(out, detect_mode(src))
        check(name, same and ast_signature(parse_source(back).statements) == base)

# every glyph in the table lexes to its token in symbol mode
for row in glyph_table()["tokens"]:
    toks = Lexer("#symbol\n" + "x " + row["symbol"] + " y\n").tokenize()
    want = TT[row["token"].upper()]
    check(f"glyph {row['symbol']} lexes as {row['token']}", any(t.type == want for t in toks))

# symbol-mode programs run identically
for ex in ("speed_test", "hello"):
    src = open(os.path.join(ROOT, "examples", ex + ".sym"), encoding="utf-8").read()
    sym_path = os.path.join(ROOT, "examples", f".{ex}.symbolmode.sym")
    open(sym_path, "w", encoding="utf-8").write(morph(src, "symbol"))
    try:
        run = lambda p: subprocess.run([sys.executable, os.path.join(ROOT, "bin", "sym.py"), "run", os.path.basename(p)],
                                       cwd=os.path.dirname(p), capture_output=True, text=True, timeout=300).stdout
        a = run(os.path.join(ROOT, "examples", ex + ".sym"))
        b = run(sym_path)
        check(f"{ex}: symbol mode prints exactly what normal mode prints", a == b and a.strip(), (a[-200:], b[-200:]))
    finally:
        for p in glob.glob(os.path.join(ROOT, "examples", f".{ex}.symbolmode*")):
            os.remove(p)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)

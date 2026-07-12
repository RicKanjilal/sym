"""
THE MATRIX — every consumer × every provider.

Runs each language's client selftest and renders the full grid.
This is the artifact: 10 consumers, 8 providers, one hub, zero
direct language-to-language connections.

    python3 tests/test_matrix.py
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
CLIENTS = os.path.join(ROOT, "clients")
ENV = {**os.environ, "SYM_HOME": ROOT}

PROVIDERS = ["java", "js", "python", "php", "ruby", "r", "perl", "c"]


def _build_java():
    cls = os.path.join(CLIENTS, "java", "Sym.class")
    if not os.path.exists(cls):
        subprocess.run(["javac", "Sym.java"], cwd=os.path.join(CLIENTS, "java"),
                       check=True, capture_output=True)
    return ["java", "-cp", os.path.join(CLIENTS, "java"), "Sym"]


def _build_go():
    binary = "/tmp/sym-matrix-go"
    if not os.path.exists(binary):
        subprocess.run(["go", "build", "-o", binary, "sym.go"],
                       cwd=os.path.join(CLIENTS, "go"), check=True,
                       capture_output=True,
                       env={**ENV, "GOCACHE": "/tmp/gocache", "GO111MODULE": "off"})
    return [binary]


def _build_rust():
    binary = "/tmp/sym-matrix-rust"
    if not os.path.exists(binary):
        subprocess.run(["rustc", "-O", "-o", binary, "sym.rs"],
                       cwd=os.path.join(CLIENTS, "rust"), check=True,
                       capture_output=True)
    return [binary]


CONSUMERS = {
    "sym":    lambda: [sys.executable, os.path.join(ROOT, "bin", "sym.py"),
                       "run", "matrix_row.sym"],
    "python": lambda: [sys.executable, os.path.join(CLIENTS, "python", "sym.py")],
    "node":   lambda: ["node", os.path.join(CLIENTS, "node", "sym.mjs")],
    "ruby":   lambda: ["ruby", os.path.join(CLIENTS, "ruby", "sym.rb")],
    "php":    lambda: ["php", os.path.join(CLIENTS, "php", "sym.php")],
    "perl":   lambda: ["perl", "-CS", os.path.join(CLIENTS, "perl", "sym.pl")],
    "r":      lambda: ["Rscript", os.path.join(CLIENTS, "r", "sym.R")],
    "java":   _build_java,
    "go":     _build_go,
    "rust":   _build_rust,
}

_RUNTIME_OF = {"sym": "python3", "python": "python3", "node": "node",
               "ruby": "ruby", "php": "php", "perl": "perl", "r": "Rscript",
               "java": "javac", "go": "go", "rust": "rustc"}


def run_matrix():
    grid = {}
    for name, argv_fn in CONSUMERS.items():
        if shutil.which(_RUNTIME_OF[name]) is None:
            grid[name] = None
            continue
        try:
            argv = argv_fn()
            r = subprocess.run(argv, cwd=EXAMPLES, env=ENV, timeout=600,
                               capture_output=True, text=True)
            out = r.stdout
        except Exception as e:
            out = f"(runner error: {e})"
        if name == "sym":
            # the .sym row prints provider=value lines
            expect = {"java": "java=32", "js": "js=X!!!", "python": "python=9",
                      "php": "php=SYM", "ruby": "ruby=12", "r": "r=3",
                      "perl": "perl=3", "c": "c=1.414"}
            grid[name] = {p: (expect[p] in out) for p in PROVIDERS}
        else:
            row = {}
            for p in PROVIDERS:
                row[p] = bool(re.search(
                    rf"(✅|\\u2705).*{name}.*(→|\\u2192|->).*\b{p}\b", out)) or \
                    f"✅ {name} → {p}" in out
            grid[name] = row
        ok_all = grid[name] and all(grid[name].values())
        status = "row OK" if ok_all else "INCOMPLETE"
        print(f"  {name:8s} {status}")
    return grid


def render(grid):
    cols = PROVIDERS
    print()
    print("  THE MATRIX — consumers (rows) × providers (columns)")
    print("  " + " " * 9 + "".join(f"{c:>8s}" for c in cols))
    for name, row in grid.items():
        if row is None:
            print(f"  {name:9s}" + "".join(f"{'skip':>8s}" for _ in cols))
            continue
        cells = "".join(f"{'✅' if row[p] else '❌':>7s}" for p in cols)
        print(f"  {name:9s}{cells}")
    print()


if __name__ == "__main__":
    grid = run_matrix()
    render(grid)
    ran = [r for r in grid.values() if r is not None]
    total = sum(len(r) for r in ran)
    passed = sum(sum(1 for v in r.values() if v) for r in ran)
    print(f"  {passed}/{total} cells green across {len(ran)} consumers")
    sys.exit(0 if passed == total else 1)

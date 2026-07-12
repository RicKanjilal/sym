#!/usr/bin/env python3
"""
Sym CLI v0.2.0
Usage:
    sym run <file.sym>              Run (hybrid: C for pure funcs, Python for libs)
    sym run --python <file.sym>     Force pure Python mode
    sym build -t native <file.sym>  Compile to native binary
    sym build -t c <file.sym>       Emit C source
    sym build -t python <file.sym>  Emit Python source
    sym build -t hybrid <file.sym>  Build .so + Python wrapper
    sym check <file.sym>            Type-check and analyze
    sym repl                        Interactive REPL
    sym version                     Show version
"""

import sys
import os
import argparse
import subprocess
import traceback
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from compiler.lexer import lex
from compiler.parser import parse
from compiler.codegen_python import generate_python
from compiler.codegen_c import generate_c, compile_c
from compiler.ast_nodes import FunctionDef

VERSION = "0.2.0"
BANNER = f"""
  ⚡ Sym v{VERSION}
  ─────────────────────────────────────
  Python's ecosystem. C's speed. One language.
"""


def _has_cc():
    for cc in ["gcc", "clang", "cc"]:
        try:
            r = subprocess.run([cc, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0: return True
        except: pass
    return False


def _cleanup(paths):
    for p in paths:
        if p and os.path.exists(p):
            try: os.remove(p)
            except: pass


def _run_py(path, cwd, verbose):
    ppath = ROOT + ":" + os.environ.get("PYTHONPATH", "")
    if verbose:
        print(f"  ⚙ Executing...")
        print(f"  {'─' * 40}")
    r = subprocess.run([sys.executable, path], cwd=cwd, env={**os.environ, "PYTHONPATH": ppath})
    sys.exit(r.returncode)


# ── RUN ────────────────────────────────────────────────────────
def cmd_run(args):
    fp = args.file
    if not os.path.exists(fp):
        print(f"❌ File not found: {fp}"); sys.exit(1)

    src = open(fp).read()
    from compiler.preprocess import preprocess as _pp; src = _pp(src)
    _compile_sym_deps(src, fp)
    fname = os.path.basename(fp)
    v = args.verbose
    out_dir = os.path.dirname(os.path.abspath(fp))
    gen_py = os.path.join(out_dir, f".{fname}.gen.py")
    cleanup = [gen_py]

    try:
        if v: print(f"  ⚙ Lexing {fname}...")
        tokens = lex(src, fname)
        if args.tokens:
            for t in tokens: print(f"  {t}")
            return

        if v: print(f"  ⚙ Parsing...")
        program = parse(tokens, fname)
        if args.ast:
            _print_ast(program); return

        # ── Hybrid path ──
        if not args.python and _has_cc():
            try:
                from compiler.hybrid import HybridCodegen, compile_shared_library, PurityAnalyzer

                analyzer = PurityAnalyzer()
                pure_funcs, _ = analyzer.analyze(program)

                if pure_funcs:
                    pnames = [f.name for f in pure_funcs]
                    if v:
                        print(f"  ⚡ Hybrid mode: {len(pure_funcs)} functions → C")
                        print(f"    Native: {', '.join(pnames)}")

                    hybrid = HybridCodegen()
                    c_src, py_src, pnames = hybrid.generate(program)

                    if args.emit:
                        print("/* ═══ C NATIVE ═══ */")
                        print(c_src)
                        print("# ═══ PYTHON HYBRID ═══")
                        print(py_src)
                        return

                    # Compile .so
                    if c_src:
                        t0 = _time.time()
                        ok = compile_shared_library(c_src, out_dir)
                        if ok:
                            so_kb = os.path.getsize(os.path.join(out_dir, "_sym_native.so")) / 1024
                            if v: print(f"  ✅ Native library: {so_kb:.1f} KB ({(_time.time()-t0)*1000:.0f}ms)")
                        else:
                            # Remove stale .so so Python fallback works
                            stale_so = os.path.join(out_dir, "_sym_native.so")
                            if os.path.exists(stale_so):
                                os.remove(stale_so)
                            if v: print(f"  ⚠ C compilation failed → Python fallback")

                    with open(gen_py, "w") as f: f.write(py_src)
                    try: _run_py(gen_py, out_dir, v)
                    finally:
                        if not args.keep: _cleanup(cleanup)
                    return
            except Exception as e:
                if v: print(f"  ⚠ Hybrid failed: {e} → Python fallback")

        # ── Pure Python path ──
        if v: print(f"  ⚙ Python mode...")
        py_code = generate_python(program, os.path.basename(fp))
        if args.emit:
            print(py_code); return

        with open(gen_py, "w") as f: f.write(py_code)
        try: _run_py(gen_py, out_dir, v)
        finally:
            if not args.keep: _cleanup(cleanup)

    except SyntaxError as e:
        print(f"❌ Syntax Error: {e}"); sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        if v: traceback.print_exc()
        sys.exit(1)


# ── BUILD ──────────────────────────────────────────────────────
def cmd_build(args):
    fp = args.file
    if not os.path.exists(fp):
        print(f"❌ File not found: {fp}"); sys.exit(1)

    src = open(fp).read()
    from compiler.preprocess import preprocess as _pp; src = _pp(src)
    _compile_sym_deps(src, fp)
    fname = os.path.basename(fp)
    target = args.target or "python"

    try:
        tokens = lex(src, fname)
        program = parse(tokens, fname)

        if target == "native":
            c_code = generate_c(program)
            out = args.output or fp.replace(".sym", "")
            if out.endswith(".sym"): out = out[:-4]
            print(f"  ⚙ {fname} → C → native binary...")
            ok = compile_c(c_code, out)
            if ok:
                kb = os.path.getsize(out) / 1024
                print(f"  ✅ Built: {out} ({kb:.1f} KB)")
                print(f"  🔥 Run: ./{os.path.basename(out)}")
            else: sys.exit(1)

        elif target == "c":
            c_code = generate_c(program)
            out = args.output or fp.replace(".sym", ".c")
            with open(out, "w") as f: f.write(c_code)
            print(f"  ✅ {fp} → {out}")

        elif target == "hybrid":
            from compiler.hybrid import HybridCodegen, compile_shared_library
            hybrid = HybridCodegen()
            c_src, py_src, pnames = hybrid.generate(program)
            out_dir = os.path.dirname(os.path.abspath(fp))

            if c_src:
                compile_shared_library(c_src, out_dir)
                print(f"  ✅ C library: {len(pnames)} functions → _sym_native.so")
                if pnames: print(f"    Native: {', '.join(pnames)}")

            py_out = fp.replace(".sym", "_hybrid.py")
            with open(py_out, "w") as f: f.write(py_src)
            print(f"  ✅ Python: {py_out}")

        elif target in ("js", "ts"):
            from compiler.codegen_js import generate_js
            code = generate_js(program, typescript=(target == "ts"))
            ext = ".ts" if target == "ts" else ".js"
            out = args.output or fp.replace(".sym", ext)
            with open(out, "w") as f: f.write(code)
            print(f"  ✅ {fp} → {out}")

        else:  # python
            py_code = generate_python(program, os.path.basename(fp))
            out = args.output or fp.replace(".sym", ".py")
            with open(out, "w") as f: f.write(py_code)
            print(f"  ✅ {fp} → {out}")

    except SyntaxError as e:
        print(f"❌ Syntax Error: {e}"); sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}"); traceback.print_exc(); sys.exit(1)


# ── CHECK ──────────────────────────────────────────────────────
def cmd_add(args):
    """Universal package install: pip / npm / cargo behind one command.
    sym add numpy         -> pip
    sym add js:react      -> npm
    sym add rust:serde    -> cargo
    sym add react --eco js
    """
    import subprocess
    pkg = args.package
    eco = args.eco
    if ":" in pkg:
        prefix, pkg = pkg.split(":", 1)
        eco = {"py": "py", "js": "js", "rust": "rust", "npm": "js", "cargo": "rust",
               "pip": "py", "ruby": "ruby", "gem": "ruby", "r": "r", "cran": "r",
               "php": "php", "composer": "php", "perl": "perl", "cpan": "perl",
               "java": "java", "maven": "java", "mvn": "java"}.get(prefix, eco)
    if eco == "auto":
        eco = "py"  # default ecosystem
    if eco == "java":
        from bridge.maven import add_java
        jars = add_java(pkg)
        print(f"  ✅ {len(jars)} jars ready (~/.sym/jars)")
        return
    cmds = {
        "py":   [["pip", "install", pkg], ["pip3", "install", "--user", pkg], ["pip", "install", "--break-system-packages", pkg]],
        "js":   [["npm", "install", pkg]],
        "rust": [["cargo", "add", pkg]],
        "ruby": [["gem", "install", pkg], ["gem", "install", "--user-install", pkg]],
        "r":    [["Rscript", "-e", f'install.packages("{pkg}", repos="https://cloud.r-project.org")']],
        "php":  [["composer", "require", pkg], ["composer", "global", "require", pkg]],
        "perl": [["cpan", "-T", pkg]],
        "java": None,  # handled by bridge.maven below
    }
    label = {"py": "pip", "js": "npm", "rust": "cargo", "ruby": "gem",
             "r": "CRAN", "php": "composer", "perl": "cpan"}[eco]
    print(f"  \u26a1 Installing {pkg} via {label}...")
    for c in cmds[eco]:
        try:
            r = subprocess.run(c, capture_output=True, text=True)
            if r.returncode == 0:
                print(f"  \u2705 {pkg} installed ({label})")
                return
        except FileNotFoundError:
            continue
    print(f"  \u274c Could not install {pkg}. Is {label} installed and on PATH?")

def cmd_check(args):
    fp = args.file
    if not os.path.exists(fp):
        print(f"❌ File not found: {fp}"); sys.exit(1)

    src = open(fp).read()
    from compiler.preprocess import preprocess as _pp; src = _pp(src)
    _compile_sym_deps(src, fp)
    fname = os.path.basename(fp)

    try:
        tokens = lex(src, fname)
        program = parse(tokens, fname)

        from compiler.typecheck import typecheck
        _warns = typecheck(program)
        if _warns:
            print(f"  ⚠ {len(_warns)} type warning(s):")
            for w in _warns:
                print(f"     {w}")

        from compiler.hybrid import PurityAnalyzer
        analyzer = PurityAnalyzer()
        pure_funcs, _ = analyzer.analyze(program)
        pure_names = [f.name for f in pure_funcs]
        total = sum(1 for s in program.statements if isinstance(s, FunctionDef))
        py_count = total - len(pure_names)

        print(f"  ✅ {fname}: {len(program.statements)} statements")
        if total:
            print(f"     Functions: {total} total")
            print(f"       → C:      {len(pure_names)} ({', '.join(pure_names) if pure_names else 'none'})")
            print(f"       → Python: {py_count}")
        has_cc = _has_cc()
        print(f"     C compiler: {'✅ available' if has_cc else '❌ not found (install gcc)'}")
    except SyntaxError as e:
        print(f"❌ {e}"); sys.exit(1)


# ── REPL ───────────────────────────────────────────────────────
def cmd_repl(args):
    print(BANNER)
    print("  Type Sym code. 'exit' to quit.\n")
    while True:
        try:
            source = input("sym> ").strip()
            if source in ("exit", "quit"): break
            if not source: continue
            tokens = lex(source + "\n", "<repl>")
            program = parse(tokens, "<repl>")
            py = generate_python(program, os.path.basename(fp))
            lines = [l for l in py.split("\n")
                     if not l.startswith("#") and not l.startswith("import ")
                     and not l.startswith("from ") and not l.startswith("for _p")
                     and not l.startswith("    if _p") and not l.startswith("try:")
                     and not l.startswith("except") and not l.startswith("    import")
                     and not l.startswith("    def _sym") and not l.startswith("    sqrt")
                     and not l.startswith("    PI")]
            code = "\n".join(lines).strip()
            if code: exec(code, {"__builtins__": __builtins__})
        except KeyboardInterrupt: print(); break
        except EOFError: break
        except Exception as e: print(f"  Error: {e}")


def _print_ast(node, indent=0):
    pfx = "  " * indent
    print(f"{pfx}{type(node).__name__}:", end="")
    fields = {k: v for k, v in node.__dict__.items() if k not in ("line", "col") and v}
    if not fields: print(); return
    print()
    for k, v in fields.items():
        if isinstance(v, list):
            print(f"{pfx}  {k}:")
            for item in v:
                if hasattr(item, "__dict__"): _print_ast(item, indent + 2)
                else: print(f"{pfx}    {item!r}")
        elif hasattr(v, "__dict__"):
            print(f"{pfx}  {k}:"); _print_ast(v, indent + 2)
        else:
            print(f"{pfx}  {k}: {v!r}")


def _compile_sym_deps(src, fp):
    """Auto-compile sibling .sym modules that this file imports."""
    import re, os
    from compiler.lexer import Lexer
    from compiler.parser import Parser
    from compiler.codegen_python import generate_python
    d = os.path.dirname(os.path.abspath(fp))
    mods = set(re.findall(r'(?:^|\n)\s*(?:from|import|\u2282)\s+(?:py\.)?([A-Za-z_][A-Za-z0-9_]*)', src))
    for m in mods:
        symp = os.path.join(d, m + ".sym")
        pyp = os.path.join(d, m + ".py")
        if os.path.exists(symp):
            try:
                s2 = open(symp).read()
                from compiler.preprocess import preprocess as _pp
                s2 = _pp(s2)
                toks = Lexer(s2).tokenize()
                prog = Parser(toks).parse()
                open(pyp, "w").write(generate_python(prog))
            except Exception as e:
                print(f"  \u26a0 could not compile module {m}.sym: {e}")

def main():
    p = argparse.ArgumentParser(prog="sym", description="Sym — Python's ecosystem, C's speed.")
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="Run .sym file (hybrid C+Python)")
    r.add_argument("file")
    r.add_argument("-v", "--verbose", action="store_true")
    r.add_argument("--python", action="store_true", help="Force pure Python")
    r.add_argument("--tokens", action="store_true")
    r.add_argument("--ast", action="store_true")
    r.add_argument("--emit", action="store_true")
    r.add_argument("--keep", action="store_true")
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("build", help="Compile .sym to target")
    b.add_argument("file")
    b.add_argument("-o", "--output")
    b.add_argument("-t", "--target", choices=["python", "c", "native", "hybrid", "js", "ts"], default="python")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check", help="Analyze .sym file")
    c.add_argument("file")
    c.set_defaults(func=cmd_check)

    sub.add_parser("repl", help="Interactive REPL").set_defaults(func=cmd_repl)
    sub.add_parser("version", help="Version").set_defaults(func=lambda a: print(BANNER))
    ad = sub.add_parser("add", help="Install a package (pip/npm/cargo)")
    ad.add_argument("package", help="pkg name; prefix js: or rust: to pick ecosystem")
    ad.add_argument("-e", "--eco", choices=["py","js","rust","auto"], default="auto")
    ad.set_defaults(func=cmd_add)

    args = p.parse_args()
    if not args.command: print(BANNER); p.print_help(); sys.exit(0)
    args.func(args)

if __name__ == "__main__":
    main()

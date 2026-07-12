"""
sym — the SymBridge client for Python.

    import sym

    calc = sym.java("math.Calculator")
    print(calc.add(5, 8))                 # 13, from a live JVM

    lst = sym.java("java.util.ArrayList")()
    lst.add("hello")                      # live object handle

    lodash = sym.js("lodash")
    ggplot = sym.r("ggplot2")
    libm   = sym.c("m")
    np     = sym.imp("numpy")             # registry: routes anywhere

    sym.block("ruby", 'sym["x"] = "from ruby"')
    print(sym.shared["x"])

Python is special: it runs IN-PROCESS with the broker (Python is the
host's native tongue), so sym.imp("numpy") hands you the real numpy
module at full fidelity — no serialization, no worker.

Finding the Sym installation, in order:
  1. $SYM_HOME
  2. ~/.sym
  3. walking up from this file (running from inside the repo)
"""

import os
import sys


def _find_root():
    # 0. pip-installed core (pip install sym-lang) — the packaged path
    try:
        import sym_lang
        core = os.path.join(os.path.dirname(sym_lang.__file__), "core")
        if os.path.isdir(os.path.join(core, "bridge")):
            return core
    except ImportError:
        pass
    env = os.environ.get("SYM_HOME")
    if env and os.path.isdir(os.path.join(env, "bridge")):
        return env
    home = os.path.join(os.path.expanduser("~"), ".sym")
    if os.path.isdir(os.path.join(home, "bridge")):
        return home
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        if os.path.isdir(os.path.join(d, "bridge")):
            return d
        d = os.path.dirname(d)
    raise ImportError(
        "sym: can't find a Sym installation. Set SYM_HOME or run "
        "install.sh (which puts it at ~/.sym).")


_ROOT = _find_root()
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bridge.broker import get_bridge  # noqa: E402
from bridge import extras as _extras  # noqa: E402

_b = get_bridge()
shared = _b.shared


class _JavaRoot:
    """sym.java("full.Class")  — classic import (unchanged)
    sym.java.package("org.apache.lucene") — namespace navigation
    sym.java.util.ArrayList — direct navigation from the root"""
    def __call__(self, target):
        return _b.import_module("java", target)
    def package(self, prefix):
        from bridge.broker import JavaNamespace
        return JavaNamespace(_b, prefix)
    def __getattr__(self, name):
        from bridge.broker import JavaNamespace
        return getattr(JavaNamespace(_b, "java"), name)  # sym.java.util → java.util

java = _JavaRoot()
def js(target):    return _b.import_module("js", target)
def php(target):   return _b.import_module("php", target)
def ruby(target):  return _b.import_module("ruby", target)
def r(target):     return _b.import_module("r", target)
def perl(target):  return _b.import_module("perl", target)
def c(target):     return _extras.c_import(target)


def imp(target):
    """Registry import — sym.imp('tensorflow') routes to the home ecosystem."""
    return _extras.sym_import(target, lambda: _b)


def block(lang, code):
    """Run raw code in a real foreign runtime, sharing sym.shared."""
    return _b.exec_block(lang, code)


def call(lang, target, args=None):
    return _b.call(lang, target, args or [])


def close():
    _b.close()


# ── selftest: python as consumer against every provider ─────
if __name__ == "__main__":
    def _ok(name, passed):
        print(f"  {'✅' if passed else '❌'} python → {name}")
    _ok("java", call("java", "java.lang.Math.pow", [2, 5]) == 32.0)
    _ok("js", call("js", "Math.max", [3, 9, 2]) == 9)
    import math as _m
    _ok("python", _m.sqrt(81) == 9.0)  # native tongue — in-process by design
    _ok("php", call("php", "strtoupper", ["sym"]) == "SYM")
    _ok("ruby", call("ruby", "Math.sqrt", [144]) == 12.0)
    _ok("r", call("r", "mean", [[1, 2, 3, 4, 5]]) == 3)
    perl("POSIX")
    _ok("perl", call("perl", "POSIX.floor", [3.7]) == 3)
    _ok("c", abs(c("m").call("sqrt", [2.0], "double") - 1.41421) < 0.001)
    lst = java("java.util.ArrayList")()
    lst.add("from python")
    _ok("java live object", lst.size() == 1)
    close()
    print("MATRIX_ROW_OK python")

"""
SymBridge extras:

1. C FFI — `c.import sqlite3` loads a native shared library through
   ctypes in the host. This is how "millions of C libraries" become
   reachable without a C worker: the OS's own loading mechanism IS
   the bridge. Explicit return types keep it honest.

2. The Registry — `sym.import tensorflow` without caring where it
   lives. A lookup table (plus a probe order) routes each name to its
   home ecosystem: python / js / java / r / php / ruby / c.
"""

import ctypes
import ctypes.util
import importlib
import json
import os
import subprocess
import sys


# ═══════════════════════════════════════════════════════════
# C FFI
# ═══════════════════════════════════════════════════════════

_C_TYPES = {
    "int": ctypes.c_longlong,
    "double": ctypes.c_double,
    "str": ctypes.c_char_p,
    "void": None,
    "ptr": ctypes.c_void_p,
}


class CLib:
    """Proxy for a loaded native library.
    lib.call("sqlite3_libversion", [], "str")  →  '3.45.1'
    Return type is explicit ("int"|"double"|"str"|"void"|"ptr") because
    C headers aren't available at runtime — guessing would be lying."""

    def __init__(self, name: str, cdll):
        self._name = name
        self._lib = cdll

    def call(self, func: str, args=None, ret: str = "int"):
        args = args or []
        try:
            fn = getattr(self._lib, func)
        except AttributeError:
            raise RuntimeError(f"[c ffi] '{func}' not found in {self._name}")
        if ret not in _C_TYPES:
            raise RuntimeError(f"[c ffi] unknown return type '{ret}' "
                               f"(use int/double/str/void/ptr)")
        fn.restype = _C_TYPES[ret]
        c_args = []
        for a in args:
            if isinstance(a, bool):
                c_args.append(ctypes.c_int(1 if a else 0))
            elif isinstance(a, int):
                c_args.append(ctypes.c_longlong(a))
            elif isinstance(a, float):
                c_args.append(ctypes.c_double(a))
            elif isinstance(a, str):
                c_args.append(a.encode())
            elif isinstance(a, bytes):
                c_args.append(a)
            elif a is None:
                c_args.append(None)
            else:
                raise RuntimeError(f"[c ffi] can't pass {type(a).__name__} to C")
        result = fn(*c_args)
        if ret == "str" and result is not None:
            return result.decode() if isinstance(result, bytes) else result
        return result

    def __repr__(self):
        return f"<sym.c {self._name}>"


def c_import(name: str) -> CLib:
    """Find + load a shared library: exact path → find_library → lib<name>.so"""
    candidates = []
    if os.path.sep in name or name.endswith((".so", ".dylib", ".dll")):
        candidates.append(name)
    found = ctypes.util.find_library(name)
    if found:
        candidates.append(found)
    candidates += [f"lib{name}.so", f"lib{name}.so.0", f"{name}.so",
                   f"lib{name}.dylib", f"{name}.dll"]
    last_err = None
    for c in candidates:
        try:
            return CLib(name, ctypes.CDLL(c))
        except OSError as e:
            last_err = e
    raise RuntimeError(f"[c ffi] cannot load '{name}': {last_err}")


# ═══════════════════════════════════════════════════════════
# THE REGISTRY — sym.import X, without caring where X lives
# ═══════════════════════════════════════════════════════════

_REGISTRY_FILE = os.path.join(os.path.dirname(__file__), "registry.json")
_registry_cache = None


def _registry() -> dict:
    global _registry_cache
    if _registry_cache is None:
        with open(_REGISTRY_FILE) as f:
            _registry_cache = json.load(f)
        # project-local overrides win: ./symbridge.json {"mylib": "ruby"}
        local = os.path.join(os.getcwd(), "symbridge.json")
        if os.path.exists(local):
            try:
                with open(local) as f:
                    _registry_cache.update(json.load(f))
            except Exception:
                pass
    return _registry_cache


def _python_import(name: str):
    """Python is the host's native tongue — import in-process for full
    fidelity (a numpy array stays a real numpy array), auto-pip if missing."""
    try:
        return importlib.import_module(name)
    except ImportError:
        pass
    for extra in ([], ["--break-system-packages"], ["--user"]):
        r = subprocess.run([sys.executable, "-m", "pip", "install", name] + extra,
                           capture_output=True)
        if r.returncode == 0:
            break
    return importlib.import_module(name)


def sym_import(name: str, get_bridge):
    """Route a bare name to its home ecosystem."""
    lang = _registry().get(name)
    if lang == "python" or lang is None and _probe_python(name):
        return _python_import(name)
    if lang == "c":
        return c_import(name)
    if lang:
        return get_bridge().import_module(lang, name)
    # probe order: js → java → give a useful error
    for probe in ("js", "java"):
        try:
            return get_bridge().import_module(probe, name)
        except Exception:
            continue
    raise RuntimeError(
        f"[sym.import] '{name}' not found in any ecosystem. "
        f"Route it explicitly (python/js/java/php/ruby/r/perl/c) in "
        f"./symbridge.json, e.g. {{\"{name}\": \"ruby\"}}")


def _probe_python(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False

"""
SymBridge stdio host — the broker as a service.

This is what turns Sym from "a language" into MIDDLEWARE: any program in
any language can spawn this process and speak the same JSON-line protocol
the workers speak, gaining access to every ecosystem Sym hosts.

    your_program.py / your_program.js / anything
        ↓ JSON lines
    stdio_host (this file — owns one SymBridge)
        ↓ Symbol Objects
    JVM / Node / PHP / Ruby / R / Perl / Go / Rust / C workers

Requests:
  {"id", "op": "import", "lang": "java", "target": "math.Calculator"}
  {"id", "op": "call",   "lang": "java", "target": "math.Calculator.add", "args": [5, 8]}
  {"id", "op": "new",    "lang": "java", "target": "java.util.ArrayList", "args": []}
  {"id", "op": "hcall",  "lang": "java", "handle": 42, "method": "add", "args": ["x"]}
  {"id", "op": "exec",   "lang": "ruby", "code": "..."}
  {"id", "op": "get"/"set", "key": "...", "value": ...}      (shared space)
  {"id", "op": "registry", "target": "numpy"}                 (sym.import — python
                                                               names resolve in YOUR
                                                               process, not here)
"""

import json
import sys
import os
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, _ROOT)

from bridge.broker import get_bridge, _to_symbol, ForeignHandle, ForeignModule  # noqa


# ── Python as a PROVIDER for external consumers ─────────────
# The host process IS Python, so "importing a python lib" means importing
# here and handing the consumer handles to live Python objects.
PY_HANDLES = {}
_py_hid = 0


def _py_handle(obj):
    global _py_hid
    _py_hid += 1
    PY_HANDLES[_py_hid] = obj
    return {"__sym__": "handle", "runtime": "python", "id": _py_hid,
            "type": type(obj).__name__}


def _py_to_symbol(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_py_to_symbol(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _py_to_symbol(x) for k, x in v.items()}
    try:  # numpy scalars etc. that are float-able stay data
        import numbers
        if isinstance(v, numbers.Number):
            return float(v)
    except Exception:
        pass
    return _py_handle(v)


def _py_deref(args):
    out = []
    for a in args or []:
        if isinstance(a, dict) and a.get("__sym__") == "handle"                 and a.get("runtime") == "python":
            out.append(PY_HANDLES[a["id"]])
        else:
            out.append(a)
    return out


def _py_resolve(target):
    import importlib
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        try:
            mod = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        obj = mod
        for attr in parts[i:]:
            obj = getattr(obj, attr)
        return obj
    import builtins
    obj = builtins
    try:
        for attr in target.split("."):
            obj = getattr(obj, attr)
        return obj
    except AttributeError:
        pass
    raise ImportError(f"cannot resolve python target '{target}'")


def _handle_python(msg):
    op = msg["op"]
    if op == "import":
        from bridge.extras import _python_import
        mod = _python_import(msg["target"])
        return _py_handle(mod)
    if op == "call":
        fn = _py_resolve(msg["target"])
        return _py_to_symbol(fn(*_py_deref(msg.get("args"))))
    if op == "new":
        cls = _py_resolve(msg["target"])
        return _py_handle(cls(*_py_deref(msg.get("args"))))
    if op == "hcall":
        obj = PY_HANDLES[msg["handle"]]
        fn = getattr(obj, msg["method"])
        return _py_to_symbol(fn(*_py_deref(msg.get("args"))))
    if op == "index":
        obj = PY_HANDLES[msg["handle"]]
        return _py_to_symbol(obj[_py_deref(msg.get("args"))[0]])
    if op == "getattr":
        obj = PY_HANDLES[msg["handle"]]
        return _py_to_symbol(getattr(obj, msg["name"]))
    if op == "free":
        PY_HANDLES.pop(msg["handle"], None)
        return True
    raise ValueError(f"python provider: unknown op '{op}'")


# ── C as a PROVIDER over the wire ────────────────────────────
C_LIBS = {}


def _handle_c(msg):
    from bridge.extras import c_import
    op = msg["op"]
    if op == "import":
        C_LIBS[msg["target"]] = c_import(msg["target"])
        return {"lib": msg["target"]}
    if op == "call":
        lib_name, fn = msg["target"].rsplit(".", 1)
        if lib_name not in C_LIBS:
            C_LIBS[lib_name] = c_import(lib_name)
        args = msg.get("args") or []
        if not isinstance(args, list):   # some serializers unbox 1-elem lists
            args = [args]
        # JSON can't tell 2.0 from 2 (PHP/Perl strip the .0) — argtypes settle it
        argtypes = msg.get("argtypes") or []
        if isinstance(argtypes, str):
            argtypes = [argtypes]
        coerced = []
        for i, a in enumerate(args):
            t = argtypes[i] if i < len(argtypes) else None
            if t == "double":
                coerced.append(float(a))
            elif t == "int":
                coerced.append(int(a))
            elif t == "str":
                coerced.append(str(a))
            else:
                coerced.append(a)
        ret = msg.get("ret", "int")
        return C_LIBS[lib_name].call(fn, coerced, ret)
    raise ValueError(f"c provider: unknown op '{op}'")


# Handles exported to external clients are PINNED here: the client now owns
# the lifetime (explicit free op), so host-side GC must not reap them.
EXPORTED = {}


def _pin(v):
    if isinstance(v, ForeignHandle):
        EXPORTED[(v._lang, v._id)] = v
    elif isinstance(v, list):
        for x in v:
            _pin(x)
    elif isinstance(v, dict):
        for x in v.values():
            _pin(x)
    return v


def main():
    bridge = get_bridge()
    out = sys.stdout

    def send(obj):
        out.write(json.dumps(obj) + "\n")
        out.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        try:
            op = msg.get("op")
            lang = msg.get("lang", "")
            if lang == "python" and op in ("import", "call", "new", "hcall",
                                           "index", "getattr", "free"):
                send({"id": mid, "ok": True, "value": _handle_python(msg)})
                continue
            if lang == "c" and op in ("import", "call"):
                send({"id": mid, "ok": True, "value": _handle_c(msg)})
                continue
            if op == "ping":
                send({"id": mid, "ok": True, "value": "pong"})
            elif op == "shutdown":
                send({"id": mid, "ok": True, "value": "bye"})
                bridge.close()
                return
            elif op == "import":
                mod = bridge.import_module(lang, msg["target"])
                send({"id": mid, "ok": True,
                      "value": {"__sym__": "module", "lang": lang,
                                "target": msg["target"], "meta": mod._meta}})
            elif op == "call":
                v = _pin(bridge.call(lang, msg["target"], msg.get("args") or []))
                send({"id": mid, "ok": True, "value": _to_symbol(v)})
            elif op == "new":
                v = _pin(bridge.construct(lang, msg["target"], msg.get("args") or []))
                send({"id": mid, "ok": True, "value": _to_symbol(v)})
            elif op == "hcall":
                v = _pin(bridge.handle_call(lang, msg["handle"], msg["method"],
                                            msg.get("args") or []))
                send({"id": mid, "ok": True, "value": _to_symbol(v)})
            elif op == "exec":
                v = _pin(bridge.exec_block(lang, msg["code"]))
                for sv in bridge.shared.values():
                    _pin(sv)
                send({"id": mid, "ok": True, "value": _to_symbol(v)})
            elif op == "get":
                send({"id": mid, "ok": True,
                      "value": _to_symbol(bridge.shared.get(msg["key"]))})
            elif op == "set":
                bridge.shared[msg["key"]] = msg.get("value")
                send({"id": mid, "ok": True, "value": True})
            elif op == "free":
                EXPORTED.pop((lang, msg.get("handle")), None)
                send({"id": mid, "ok": True, "value": True})
            elif op == "runtimes":
                import shutil as _sh
                send({"id": mid, "ok": True, "value": {
                    "java": bool(_sh.which("java")), "js": bool(_sh.which("node")),
                    "python": True, "php": bool(_sh.which("php")),
                    "ruby": bool(_sh.which("ruby")), "r": bool(_sh.which("Rscript")),
                    "perl": bool(_sh.which("perl")), "c": True}})
            elif op == "registry":
                from bridge.extras import _registry
                send({"id": mid, "ok": True,
                      "value": _registry().get(msg["target"])})
            else:
                raise ValueError(f"unknown op '{op}'")
        except Exception as e:
            send({"id": mid, "ok": False, "error": str(e),
                  "trace": traceback.format_exc(limit=4)})


if __name__ == "__main__":
    main()

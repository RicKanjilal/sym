"""
Introspection — the real surface of any library Sym can reach.

    from bridge.introspect import describe
    describe("java", "java.util.ArrayList")   # methods with real signatures
    describe("java", "org.apache.lucene")      # a package: sub-packages + classes
    describe("js", "lodash")
    describe("python", "numpy.linalg")

Python is described in a separate interpreter (importing a library can have
side effects; an editor asking for completions must never run them in its own
process). Every other language asks its worker. Results are cached on disk,
keyed by lang + target + the files that could change the answer.

Every answer has the shape
    {"kind": "class|package|module|function|...", "name": str,
     "members": [{"name", "kind", "signature", "static", "detail"?}]}
"""

import hashlib
import json
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "sym", "describe")
_TTL = 6 * 3600

_PY_DESCRIBE = r'''
import importlib, inspect, json, sys
target = sys.argv[1]
def sig(name, obj):
    try: return name + str(inspect.signature(obj))
    except (TypeError, ValueError): return name + "(...)"
def kind(v):
    if inspect.isclass(v): return "class"
    if inspect.ismodule(v): return "module"
    if callable(v): return "function"
    return "constant"
obj, parts = None, target.split(".")
for i in range(len(parts), 0, -1):
    try:
        obj = importlib.import_module(".".join(parts[:i]))
        for p in parts[i:]: obj = getattr(obj, p)
        break
    except Exception:
        obj = None
if obj is None:
    print(json.dumps({"error": f"cannot import {target}"})); sys.exit(0)
members = []
names = getattr(obj, "__all__", None) if inspect.ismodule(obj) else None
for n in sorted(names or dir(obj)):
    if n.startswith("_") and n not in ("__init__", "__call__"): continue
    try: v = getattr(obj, n)
    except Exception: continue
    k = kind(v)
    doc = (inspect.getdoc(v) or "").split("\n\n")[0][:400] if k != "constant" else ""
    s = sig(n, v) if k in ("function", "class") else f"{n}: {type(v).__name__}"
    members.append({"name": n, "kind": k, "signature": s, "static": not inspect.isclass(obj), "detail": doc})
    if len(members) >= 800: break
print(json.dumps({"kind": kind(obj), "name": target, "members": members,
                  "doc": (inspect.getdoc(obj) or "")[:800]}))
'''

LANGS = ("python", "java", "js", "ruby", "php", "perl", "r")


def _key(lang, target, cwd):
    stamp = ""
    for marker in ("package.json", "pom.xml", "Gemfile.lock", "composer.lock", "requirements.txt", "javalib"):
        p = os.path.join(cwd, marker)
        if os.path.exists(p):
            stamp += f"{marker}:{os.path.getmtime(p)};"
    return hashlib.sha1(f"{lang}|{target}|{cwd}|{stamp}".encode()).hexdigest()


def describe(lang: str, target: str, cwd: str = None, use_cache: bool = True, timeout: float = 60):
    lang = {"javascript": "js", "node": "js", "py": "python", "R": "r"}.get(lang, lang)
    if lang not in LANGS:
        raise ValueError(f"cannot describe {lang} libraries (supported: {', '.join(LANGS)})")
    cwd = os.path.abspath(cwd or os.getcwd())
    key = _key(lang, target, cwd)
    path = os.path.join(_CACHE, key + ".json")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < _TTL:
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    if lang == "python":
        out = subprocess.run([sys.executable, "-c", _PY_DESCRIBE, target], cwd=cwd,
                             capture_output=True, text=True, timeout=timeout)
        lines = [l for l in out.stdout.splitlines() if l.startswith("{")]
        if not lines:
            raise RuntimeError(out.stderr.strip()[-400:] or f"cannot describe {target}")
        result = json.loads(lines[-1])
        if "error" in result:
            raise RuntimeError(result["error"])
    else:
        prev = os.getcwd()
        os.chdir(cwd)
        try:
            from bridge.broker import get_bridge
            result = get_bridge().describe(lang, target)
        finally:
            os.chdir(prev)
    result.setdefault("lang", lang)
    try:
        os.makedirs(_CACHE, exist_ok=True)
        with open(path, "w") as f:
            json.dump(result, f)
    except OSError:
        pass
    return result

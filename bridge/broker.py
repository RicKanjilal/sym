"""
SymBridge — the polyglot broker.

Sym is the HOST. It launches language runtimes (Java, JS, ...) as
managed worker subprocesses and brokers all communication through
Symbol Objects (a neutral JSON representation). Languages never talk to
each other — they talk to Sym.

    Python call → Symbol Object → worker (JVM / Node) → Symbol Object → Python

Wire protocol: newline-delimited JSON over stdin/stdout.
  request : {"id": int, "op": "import"|"call"|"exec"|"ping", ...}
  response: {"id": int, "ok": true,  "value": <SymbolObject>, "exports": {...}}
            {"id": int, "ok": false, "error": "...", "trace": "..."}
"""

import atexit
import json
import os
import subprocess
import sys
import threading
import weakref

_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_WORKERS_DIR = os.path.join(_ROOT, "bridge", "workers")


class BridgeError(RuntimeError):
    """Raised when a foreign runtime reports an error."""


class _Worker:
    """One managed language runtime (a subprocess Sym conducts)."""

    def __init__(self, lang: str, argv, cwd=None):
        self.lang = lang
        self._id = 0
        self._lock = threading.Lock()
        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=sys.stderr,  # worker diagnostics pass through
                cwd=cwd or os.getcwd(),
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            raise BridgeError(
                f"[symbridge] runtime for '{lang}' not found. "
                f"Install it and make sure '{argv[0]}' is on PATH."
            )
        # handshake
        resp = self.request({"op": "ping"})
        if resp.get("value") != "pong":
            raise BridgeError(f"[symbridge] {lang} worker failed handshake")

    def request(self, msg: dict) -> dict:
        with self._lock:
            self._id += 1
            msg["id"] = self._id
            if self.proc.poll() is not None:
                raise BridgeError(f"[symbridge] {self.lang} worker died")
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise BridgeError(
                        f"[symbridge] {self.lang} worker closed unexpectedly")
                line = line.strip()
                if not line:
                    continue
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    # worker printed something that isn't protocol — surface it
                    print(f"[{self.lang}] {line}", file=sys.stderr)
                    continue
                if resp.get("id") == self._id:
                    break
        if not resp.get("ok"):
            err = resp.get("error", "unknown error")
            trace = resp.get("trace", "")
            raise BridgeError(f"[{self.lang}] {err}" + (f"\n{trace}" if trace else ""))
        return resp

    def close(self):
        try:
            if self.proc.poll() is None:
                try:
                    self.proc.stdin.write(json.dumps({"id": 0, "op": "shutdown"}) + "\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
                self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()


class SymBridge:
    """The host. Lazily launches workers, routes Symbol Objects."""

    def __init__(self):
        self._workers = {}
        self.shared = {}  # data shared across language blocks
        atexit.register(self.close)

    # ── worker lifecycle ─────────────────────────────────────
    _LAUNCH = {
        "js":   lambda: ["node", os.path.join(_WORKERS_DIR, "worker.mjs")],
        "php":  lambda: ["php", os.path.join(_WORKERS_DIR, "worker.php")],
        "ruby": lambda: ["ruby", os.path.join(_WORKERS_DIR, "worker.rb")],
        "r":    lambda: ["Rscript", os.path.join(_WORKERS_DIR, "worker.R")],
        "perl": lambda: ["perl", os.path.join(_WORKERS_DIR, "worker.pl")],
    }

    def _launch(self, lang: str) -> _Worker:
        if lang in self._LAUNCH:
            argv = self._LAUNCH[lang]()
        elif lang == "java":
            _ensure_java_worker_compiled()
            argv = ["java", "-cp", _classpath(), "SymWorker"]
        else:
            raise BridgeError(f"[symbridge] unsupported language: {lang}")
        return _Worker(lang, argv)

    def worker(self, lang: str) -> _Worker:
        if lang not in self._workers:
            self._workers[lang] = self._launch(lang)
        return self._workers[lang]

    def close(self):
        for w in self._workers.values():
            w.close()
        self._workers.clear()

    # ── public API used by generated Sym code ────────
    def import_module(self, lang: str, target: str):
        """java.import math.Calculator  /  js.import lodash"""
        resp = self.worker(lang).request({"op": "import", "target": target})
        return ForeignModule(self, lang, target, resp.get("value") or {})

    def call(self, lang: str, target: str, args):
        resp = self.worker(lang).request(
            {"op": "call", "target": target, "args": _to_symbol(args)})
        return _from_symbol(resp.get("value"), self, lang)

    def construct(self, lang: str, target: str, args):
        """Calculator() / Cls.new() → live object in its runtime, handle here."""
        resp = self.worker(lang).request(
            {"op": "new", "target": target, "args": _to_symbol(args)})
        return _from_symbol(resp.get("value"), self, lang)

    def free_handle(self, lang: str, handle_id):
        """Best-effort: tell the worker to drop the object. Called by GC
        finalizers — must never raise, even at interpreter shutdown."""
        try:
            w = self._workers.get(lang)
            if w is None or w.proc.poll() is not None:
                return
            w.request({"op": "free", "handle": handle_id})
        except Exception:
            pass

    def handle_call(self, lang: str, handle_id, method: str, args):
        op = "call" if lang == "java" else "hcall"  # java folds hcall into call
        msg = {"op": op, "handle": handle_id, "method": method,
               "args": _to_symbol(args)}
        resp = self.worker(lang).request(msg)
        return _from_symbol(resp.get("value"), self, lang)

    def exec_block(self, lang: str, code: str):
        """Run a  java> { }  /  js> { }  /  go> { }  block. Shared vars flow both ways."""
        if lang in ("go", "rust"):
            from bridge.compiled import run_compiled_block
            value, exports = run_compiled_block(lang, code, _to_symbol(self.shared))
            for k, v in (exports or {}).items():
                self.shared[k] = _from_symbol(v)
            return _from_symbol(value)
        resp = self.worker(lang).request(
            {"op": "exec", "code": code, "env": _to_symbol(self.shared)})
        exports = resp.get("exports") or {}
        for k, v in exports.items():
            self.shared[k] = _from_symbol(v, self, lang)
        return _from_symbol(resp.get("value"), self, lang)


class ForeignHandle:
    """A live object living in a foreign runtime. Sym holds only the ticket:
    {"__sym__": "handle", "runtime": "java", "id": 42}. Every method call
    routes back to the object where it lives."""

    def __init__(self, bridge, lang, handle_id, type_name=""):
        self._bridge = bridge
        self._lang = lang
        self._id = handle_id
        self._type = type_name
        # distributed GC: when Python drops this proxy, the worker frees
        # the real object. Best-effort by design — a dead worker already
        # freed everything.
        self._finalizer = weakref.finalize(
            self, SymBridge.free_handle, bridge, lang, handle_id)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        def method(*args):
            return self._bridge.handle_call(self._lang, self._id, name, list(args))
        return method

    def __getitem__(self, key):
        resp = self._bridge.worker(self._lang).request(
            {"op": "index", "handle": self._id, "args": _to_symbol([key])})
        return _from_symbol(resp.get("value"), self._bridge, self._lang)

    def __repr__(self):
        return f"<sym.{self._lang} handle #{self._id} {self._type}>"


class JavaNamespace:
    """Lazy dotted-path navigation: lucene.document.Field.Store.YES.
    Each attribute step resolves eagerly in the JVM: package -> deeper
    namespace, class -> callable class proxy, static field/enum -> value."""

    def __init__(self, bridge, path):
        self._bridge = bridge
        self._path = path

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        path = f"{self._path}.{name}" if self._path else name
        resp = self._bridge.worker("java").request(
            {"op": "resolve", "target": path})
        info = resp.get("value") or {}
        kind = info.get("kind")
        if kind == "class":
            return ForeignModule(self._bridge, "java", info["name"], {})
        if kind == "value":
            return _from_symbol(info.get("value"), self._bridge, "java")
        if kind == "none":
            raise AttributeError(f"[java] {info.get('error')}")
        return JavaNamespace(self._bridge, path)

    def __repr__(self):
        return f"<sym.java namespace {self._path}>"


class ForeignModule:
    """Proxy for an imported foreign module/class. Attribute access chains
    into method paths; calling fires a brokered request."""

    def __init__(self, bridge, lang, target, meta, path=""):
        self._bridge = bridge
        self._lang = lang
        self._target = target
        self._meta = meta
        self._path = path

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        new_path = f"{self._path}.{name}" if self._path else name
        if self._lang == "java" and not self._path:
            # static field? enum constant? nested class? resolve eagerly;
            # unknown names stay lazy (they're method calls)
            try:
                resp = self._bridge.worker("java").request(
                    {"op": "resolve", "target": f"{self._target}.{name}"})
                info = resp.get("value") or {}
                if info.get("kind") == "value":
                    return _from_symbol(info["value"], self._bridge, "java")
                if info.get("kind") == "class" and info.get("name") != self._target:
                    return ForeignModule(self._bridge, "java", info["name"], {})
            except Exception:
                pass
        return ForeignModule(self._bridge, self._lang, self._target,
                             self._meta, new_path)

    def __call__(self, *args):
        if not self._path:
            # Calculator() — bare call on an imported class constructs it
            return self._bridge.construct(self._lang, self._target, list(args))
        if self._path == "new" or self._path.endswith(".new"):
            # Cls.new(...) — explicit constructor, works in every runtime
            inner = self._path[:-4].rstrip(".")
            target = f"{self._target}.{inner}" if inner else self._target
            return self._bridge.construct(self._lang, target, list(args))
        target = f"{self._target}.{self._path}"
        return self._bridge.call(self._lang, target, list(args))

    def __repr__(self):
        return f"<sym.{self._lang} {self._target}{'.' + self._path if self._path else ''}>"


# ── Symbol Objects ───────────────────────────────────────────
# The neutral representation. JSON natives pass through; everything else
# is tagged so no language ever sees another language's raw objects.

def _to_symbol(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_to_symbol(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _to_symbol(x) for k, x in v.items()}
    if isinstance(v, bytes):
        import base64
        return {"__sym__": "bytes", "b64": base64.b64encode(v).decode()}
    if isinstance(v, ForeignHandle):
        return {"__sym__": "handle", "runtime": v._lang, "id": v._id}
    if isinstance(v, ForeignModule):
        raise BridgeError(
            "[symbridge] pass data or object handles, not module proxies.")
    # last resort: stringify, but tag it honestly
    return {"__sym__": "opaque", "repr": repr(v), "type": type(v).__name__}


def _from_symbol(v, bridge=None, lang=None):
    if isinstance(v, list):
        return [_from_symbol(x, bridge, lang) for x in v]
    if isinstance(v, dict):
        tag = v.get("__sym__")
        if tag == "bytes":
            import base64
            return base64.b64decode(v["b64"])
        if tag == "opaque":
            return v.get("repr")
        if tag == "handle":
            if bridge is None:
                return v  # raw tag when no bridge context (e.g. round-trip tests)
            return ForeignHandle(bridge, v.get("runtime", lang), v["id"],
                                 v.get("type", ""))
        return {k: _from_symbol(x, bridge, lang) for k, x in v.items()}
    return v


def _ensure_java_worker_compiled():
    """Compile SymWorker.java once on first use — fresh machines just work.
    Freshness by CONTENT HASH, not mtime: zips, cp and clock skew all lie
    about mtimes, and a stale .class silently speaks an old protocol."""
    import hashlib
    cls = os.path.join(_WORKERS_DIR, "SymWorker.class")
    src = os.path.join(_WORKERS_DIR, "SymWorker.java")
    stamp = os.path.join(_WORKERS_DIR, ".symworker.srchash")
    with open(src, "rb") as f:
        current = hashlib.sha256(f.read()).hexdigest()
    if os.path.exists(cls) and os.path.exists(stamp):
        with open(stamp) as f:
            if f.read().strip() == current:
                return
    import shutil
    javac = shutil.which("javac")
    if not javac:
        raise BridgeError(
            "[symbridge] Java support needs a JDK (javac not found). "
            "A JRE alone can't compile the worker. Install a JDK, e.g. "
            "openjdk-21-jdk.")
    r = subprocess.run([javac, src], capture_output=True, text=True,
                       cwd=_WORKERS_DIR)
    if r.returncode != 0:
        raise BridgeError(f"[symbridge] worker compile failed:\n{r.stderr}")
    with open(stamp, "w") as f:
        f.write(current)


# ── Java classpath: worker classes + user javalib + cwd ─────
def _classpath():
    parts = [_WORKERS_DIR, os.getcwd(),
             os.path.join(os.getcwd(), "javalib")]
    # every jar ever `sym add --java`-ed rides along
    jar_dir = os.environ.get("SYM_JAR_DIR",
        os.path.join(os.path.expanduser("~"), ".sym", "jars"))
    if os.path.isdir(jar_dir):
        parts.append(os.path.join(jar_dir, "*"))
    env_cp = os.environ.get("SYM_JAVA_CLASSPATH")
    if env_cp:
        parts.append(env_cp)
    return os.pathsep.join(parts)


# module-level singleton — one host per program
_bridge = None

def get_bridge() -> SymBridge:
    global _bridge
    if _bridge is None:
        _bridge = SymBridge()
    return _bridge

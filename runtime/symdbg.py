"""symdbg — Sym's debugger, live-value tracer and profiler.

Runs a program (a Sym program's generated Python, or any .py file) with one of three
hooks, and talks to the editor over a TCP socket as JSON lines:

    SYMDBG_MODE=debug    breakpoints, stepping, variables, evaluate; steps into js> blocks
    SYMDBG_MODE=trace    records what every line did ("x = 5", hit counts) — live values
    SYMDBG_MODE=profile  time spent in each function — the speed coach compares runs

    SYMDBG_ADDR=127.0.0.1:PORT   where the editor listens (optional for trace/profile)
    SYMDBG_OUT=/path.json        trace/profile results are also written here at exit

Usage:  python3 -m runtime.symdbg program.py [args…]     (`sym run --debug` does this)

Locations are always reported in the user's terms: Sym files and Sym line numbers when the
code came from Sym (through the generated file's __SYM_LINEMAP__), plain file/line otherwise.
"""
import json
import os
import queue
import reprlib
import runpy
import socket
import sys
import threading
import time
import traceback

MODE = os.environ.get("SYMDBG_MODE", "debug")
ADDR = os.environ.get("SYMDBG_ADDR", "")
OUT = os.environ.get("SYMDBG_OUT", "")
HERE = os.path.dirname(os.path.abspath(__file__))
_SYM_ROOT = os.path.dirname(HERE)
_SKIP_DIRS = (HERE, os.path.dirname(os.__file__))

_repr = reprlib.Repr()
_repr.maxstring = 80
_repr.maxother = 80
_repr.maxlist = 8
_repr.maxdict = 6
_repr.maxset = 6
_repr.maxtuple = 8
_repr.maxlevel = 3


def short(v, limit=120):
    try:
        s = _repr.repr(v)
    except Exception as e:  # a __repr__ that throws shouldn't kill the debugger
        s = f"<{type(v).__name__}: repr failed: {e}>"
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ── the wire ─────────────────────────────────────────────────────────────
class Channel:
    def __init__(self, addr):
        self.sock = None
        self.lock = threading.Lock()
        self.inbox = queue.Queue()
        if not addr:
            return
        host, port = addr.rsplit(":", 1)
        for _ in range(50):
            try:
                self.sock = socket.create_connection((host, int(port)), timeout=5)
                break
            except OSError:
                time.sleep(0.1)
        if self.sock is None:
            print(f"symdbg: couldn't reach the editor at {addr}; running without it", file=sys.stderr)
            return
        self.sock.settimeout(None)
        threading.Thread(target=self._read, daemon=True).start()

    @property
    def live(self):
        return self.sock is not None

    def send(self, obj):
        if not self.sock:
            return
        data = (json.dumps(obj, default=str) + "\n").encode()
        with self.lock:
            try:
                self.sock.sendall(data)
            except OSError:
                self.sock = None

    def _read(self):
        buf = b""
        while True:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                chunk = b""
            if not chunk:
                self.inbox.put({"cmd": "disconnect"})
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("cmd") == "pause":
                        DBG.pause_requested = True
                    if getattr(DBG, "js", None) is not None or (msg.get("cmd") == "setBreakpoints" and getattr(DBG, "node", None) is not None):
                        try:
                            DBG.handle_js(msg)
                        except Exception:
                            traceback.print_exc()
                        if msg.get("cmd") != "setBreakpoints":
                            continue
                    self.inbox.put(msg)


# ── where are we, in the user's terms ────────────────────────────────────
_user_cache = {}


def is_user_code(code):
    fn = code.co_filename
    hit = _user_cache.get(fn)
    if hit is not None:
        return hit
    ok = True
    if fn.startswith("<") or "site-packages" in fn or "dist-packages" in fn:
        ok = False
    else:
        a = os.path.abspath(fn)
        for d in _SKIP_DIRS:
            if a.startswith(d + os.sep):
                ok = False
        # Sym's own runtime and bridge are plumbing, not the user's program
        for sub in ("bridge", "runtime", "compiler", "bin"):
            if a.startswith(os.path.join(_SYM_ROOT, sub) + os.sep):
                ok = False
    _user_cache[fn] = ok
    return ok


def location(frame):
    """(file, line, lang) for a frame — Sym file/line when the code came from Sym."""
    g = frame.f_globals
    lm = g.get("__SYM_LINEMAP__")
    if lm is not None:
        line = lm.get(frame.f_lineno)
        src = g.get("__SYM_SRC__") or "source.sym"
        d = os.path.dirname(os.path.abspath(frame.f_code.co_filename))
        return os.path.join(d, os.path.basename(src)), line, "sym"
    if frame.f_code.co_filename.endswith(".sym.gen.py"):
        # preamble before the map exists, or the hybrid path (which has no map)
        a = os.path.abspath(frame.f_code.co_filename)
        return os.path.join(os.path.dirname(a), os.path.basename(a)[1:-len(".gen.py")]), None, "sym"
    return os.path.abspath(frame.f_code.co_filename), frame.f_lineno, "python"


_def_cache = {}


def def_line(file, name):
    """Line of `fn name` in a Sym source (any spelling of fn)."""
    import re
    if file not in _def_cache:
        try:
            _def_cache[file] = open(file, encoding="utf-8").read().splitlines()
        except OSError:
            _def_cache[file] = []
    pat = re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|ƒ|function|def)\s+" + re.escape(name) + r"\b")
    for i, l in enumerate(_def_cache[file]):
        if pat.match(l):
            return i + 1
    return 0


def frame_name(frame):
    n = frame.f_code.co_name
    if n == "<module>":
        f, _, _ = location(frame)
        return os.path.basename(f)
    return n


_HIDDEN_PREFIX = ("__", "_sym", "_c_lib", "_so_path", "_sys2", "_tb2", "_os", "_copy")


_MISSING = object()


def visible(name, value, module_scope):
    if name.startswith(_HIDDEN_PREFIX) or name in ("sym",):
        return False
    if module_scope:
        import types
        if name.startswith("_"):
            return False
        rb = sys.modules.get("runtime.builtins")
        if rb is not None and getattr(rb, name, _MISSING) is value:
            return False
        if isinstance(value, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, type)):
            return False
    return True



# ── Node: js> blocks are debugged through V8's inspector, from right here ─────
# symdbg speaks the Chrome DevTools Protocol to the Node worker itself, so the editor sees
# ONE debugger: a pause inside a js> block arrives as an ordinary "stopped" event whose
# stack has the JS frames on top of the Sym frames that called the block.
import base64 as _b64
import hashlib as _hashlib
import struct as _struct

BLOCK_LINE0 = 3   # AsyncFunction prefix (2 lines) + our tag line → block text line 0


class NodeInspector:
    def __init__(self, port, on_event):
        self.port = port
        self.on_event = on_event
        self.sock = None
        self.next_id = 0
        self.waiting = {}
        self.lock = threading.Lock()
        self.scripts = {}

    def connect(self, wait=5.0):
        url = None
        deadline = time.time() + wait
        while time.time() < deadline and url is None:
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
                s.sendall(b"GET /json/list HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                data = b""
                while True:
                    c = s.recv(65536)
                    if not c:
                        break
                    data += c
                    if b"\r\n\r\n" in data:
                        h, b = data.split(b"\r\n\r\n", 1)
                        cl = [l for l in h.split(b"\r\n") if l.lower().startswith(b"content-length:")]
                        if cl and len(b) >= int(cl[0].split(b":")[1]):
                            break
                s.close()
                body = data.split(b"\r\n\r\n", 1)[1]
                lst = json.loads(body)
                if lst:
                    url = lst[0]["webSocketDebuggerUrl"]
            except (OSError, ValueError, IndexError, KeyError):
                time.sleep(0.1)
        if url is None:
            return False
        path = "/" + url.split("/", 3)[3]
        key = _b64.b64encode(os.urandom(16)).decode()
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            c = s.recv(1)
            if not c:
                return False
            head += c
        if b" 101 " not in head.split(b"\r\n")[0]:
            return False
        s.settimeout(None)
        self.sock = s
        threading.Thread(target=self._read, daemon=True).start()
        self.call("Runtime.enable")
        self.call("Debugger.enable")
        self.call("Runtime.runIfWaitingForDebugger")
        return True

    def _send_frame(self, text):
        data = text.encode()
        n = len(data)
        hdr = bytes([0x81])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + _struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + _struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            self.sock.sendall(hdr + mask + body)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            c = self.sock.recv(n - len(buf))
            if not c:
                raise OSError("closed")
            buf += c
        return buf

    def _read(self):
        parts = b""
        try:
            while True:
                b1, b2 = self._recv_exact(2)
                n = b2 & 0x7F
                if n == 126:
                    n = _struct.unpack(">H", self._recv_exact(2))[0]
                elif n == 127:
                    n = _struct.unpack(">Q", self._recv_exact(8))[0]
                payload = self._recv_exact(n) if n else b""
                op = b1 & 0x0F
                if op == 8:
                    break
                if op in (1, 0, 2):
                    parts += payload
                    if b1 & 0x80:
                        msg = json.loads(parts)
                        parts = b""
                        self._dispatch(msg)
        except (OSError, ValueError):
            pass
        self.sock = None
        for ev in list(self.waiting.values()):
            ev[1] = {"error": "closed"}
            ev[0].set()

    def _dispatch(self, msg):
        if "id" in msg:
            w = self.waiting.pop(msg["id"], None)
            if w is not None:
                w[1] = msg
                w[0].set()
            return
        if msg.get("method") == "Debugger.scriptParsed":
            p = msg["params"]
            self.scripts[p["scriptId"]] = p.get("url", "")
        try:
            self.on_event(msg.get("method"), msg.get("params") or {})
        except Exception:
            traceback.print_exc()

    def call(self, method, params=None, timeout=10.0):
        if self.sock is None:
            return {}
        self.next_id += 1
        i = self.next_id
        w = [threading.Event(), None]
        self.waiting[i] = w
        self._send_frame(json.dumps({"id": i, "method": method, "params": params or {}}))
        w[0].wait(timeout)
        r = w[1] or {}
        return r.get("result", {})


def js_value(ro):
    """A CDP RemoteObject → (display text, type name)."""
    t = ro.get("type", "")
    st = ro.get("subtype", "")
    if t == "string":
        v = ro.get("value", "")
        return short(v), "string"
    if t in ("number", "boolean", "bigint"):
        return str(ro.get("value", ro.get("unserializableValue", ""))), t
    if t == "undefined":
        return "undefined", "undefined"
    if st == "null":
        return "null", "null"
    return ro.get("description", t), (ro.get("className") or st or t)


# ── debug mode ───────────────────────────────────────────────────────────
class Debugger:
    def __init__(self, ch):
        self.ch = ch
        self.breakpoints = {}        # abs file → set(line)
        self.bp_version = 0
        self.bp_cache = {}
        self.mode = "run"            # run | in | over | out
        self.origin = None           # (frame id, line) where a step started
        self.origin_depth = 0
        self.pause_requested = False
        self.step_foreign = False    # a step-in was asked for: let js> blocks stop too
        self.last_line = {}          # id(frame) → last reported line (several Python lines = one Sym line)
        self.frames = {}
        self.refs = {}
        self.stopped_at = None
        self.node_port = int(os.environ.get("SYMDBG_NODE_PORT", "0") or 0)
        self.node = None             # NodeInspector, once a js> block needs it
        self.js = None               # the Debugger.paused params while V8 is stopped
        self.js_blocks = {}          # symblock url → (sym file, first line)
        self.js_bps = {}             # symblock url → [breakpoint ids]
        self.py_stack = []           # Sym frames under the block that's running
        self.js_refs = {}
        self.js_stepping = False
        self.js_frames = {}
        self.js_len = {}

    # configuration happens before the program runs
    def configure(self):
        if not self.ch.live:
            return
        self.ch.send({"event": "ready", "pid": os.getpid(), "mode": MODE, "nodePort": self.node_port})
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                msg = self.ch.inbox.get(timeout=deadline - time.time())
            except queue.Empty:
                return
            if self.handle(msg) == "go":
                return

    def depth(self, frame):
        d = 0
        while frame is not None:
            if is_user_code(frame.f_code):
                d += 1
            frame = frame.f_back
        return d

    def trace_call(self, frame, event, arg):
        if event != "call" or not is_user_code(frame.f_code):
            return None
        # running freely: a function with no breakpoint in it needn't be traced line by line —
        # that's what keeps fib(30) under the debugger from crawling
        if self.mode == "run" and not self.pause_requested and frame.f_code.co_name != "<module>" and not self.has_bp(frame):
            return None
        return self.trace_local

    def has_bp(self, frame):
        code = frame.f_code
        key = (code, self.bp_version)
        hit = self.bp_cache.get(key)
        if hit is None:
            file, _, _ = location(frame)
            bps = self.breakpoints.get(file)
            if not bps:
                hit = False
            else:
                lm = frame.f_globals.get("__SYM_LINEMAP__")
                lines = {ln for (_, _, ln) in code.co_lines() if ln is not None}
                if lm is not None:
                    lines = {lm[ln] for ln in lines if ln in lm}
                hit = bool(lines & bps)
            if len(self.bp_cache) > 5000:
                self.bp_cache.clear()
            self.bp_cache[key] = hit
        return hit

    def trace_local(self, frame, event, arg):
        if event == "line":
            self.on_line(frame)
        elif event == "return":
            self.last_line.pop(id(frame), None)
        return self.trace_local

    def on_line(self, frame):
        file, line, lang = location(frame)
        if line is None:
            return
        fid = id(frame)
        if self.last_line.get(fid) == line:
            return  # still the same Sym statement
        self.last_line[fid] = line
        reason = None
        if self.pause_requested:
            reason = "pause"
        elif line in self.breakpoints.get(file, ()):
            reason = "breakpoint"
        elif self.mode == "in":
            reason = "step"
        elif self.mode == "over":
            d = self.depth(frame)
            if d < self.origin_depth or (d == self.origin_depth and (fid, line) != self.origin):
                reason = "step"
        elif self.mode == "out":
            if self.depth(frame) < self.origin_depth:
                reason = "step"
        if reason:
            self.stop(frame, reason)

    def stack(self, frame, base=0):
        out = []
        if base == 0:
            self.frames = {}
        i = base
        while frame is not None:
            if is_user_code(frame.f_code):
                file, line, lang = location(frame)
                if line is not None or not out:
                    self.frames[i] = frame
                    out.append({"id": i, "name": frame_name(frame), "file": file, "line": line or 0, "lang": lang})
                    i += 1
            frame = frame.f_back
        return out

    def stop(self, frame, reason, exc=None):
        self.pause_requested = False
        self.step_foreign = False
        self.refs = {}
        frames = self.stack(frame)
        self.stopped_at = frame
        msg = {"event": "stopped", "reason": reason, "frames": frames}
        if exc is not None:
            msg["exception"] = exc
        self.ch.send(msg)
        while True:
            msg = self.ch.inbox.get()
            r = self.handle(msg)
            if r == "go":
                break
            if r == "resume":
                self.origin = (id(frame), location(frame)[1])
                self.origin_depth = self.depth(frame)
                break
        self.stopped_at = None
        self.ch.send({"event": "continued"})

    def handle(self, msg):
        cmd = msg.get("cmd")
        if cmd == "setBreakpoints":
            f = os.path.abspath(msg.get("file", ""))
            self.breakpoints[f] = set(int(x) for x in msg.get("lines", []))
            self.bp_version += 1
            return None
        if cmd in ("configurationDone", "start"):
            return "go"
        if cmd == "continue":
            self.mode = "run"
            return "go"
        if cmd == "next":
            self.mode = "over"
            return "resume"
        if cmd == "stepIn":
            self.mode = "in"
            self.step_foreign = True
            return "resume"
        if cmd == "stepOut":
            self.mode = "out"
            return "resume"
        if cmd == "pause":
            self.pause_requested = True
            return None
        if cmd == "variables":
            self.ch.send({"event": "variables", "frame": msg.get("frame", 0), "scopes": self.variables(msg.get("frame", 0))})
            return None
        if cmd == "expand":
            self.ch.send({"event": "expanded", "ref": msg.get("ref"), "items": self.expand(msg.get("ref"))})
            return None
        if cmd == "evaluate":
            self.ch.send({"event": "evaluated", "id": msg.get("id"), **self.evaluate(msg.get("frame", 0), msg.get("expr", ""))})
            return None
        if cmd in ("stop", "disconnect"):
            sys.settrace(None)
            threading.settrace(None)
            os._exit(130)
        return None

    def item(self, name, value):
        it = {"name": str(name), "value": short(value), "type": type(value).__name__}
        if isinstance(value, (dict, list, tuple, set, frozenset)) and len(value):
            ref = len(self.refs) + 1
            self.refs[ref] = value
            it["ref"] = ref
            it["count"] = len(value)
        elif hasattr(value, "__dict__") and not callable(value) and vars(value):
            ref = len(self.refs) + 1
            self.refs[ref] = value
            it["ref"] = ref
        h = getattr(value, "_handle", None) or getattr(value, "handle_id", None)
        if type(value).__name__ in ("ForeignHandle", "JavaObject", "ForeignObject"):
            it["foreign"] = True
        return it

    def variables(self, fid):
        frame = self.frames.get(fid)
        if frame is None:
            return []
        module_scope = frame.f_code.co_name == "<module>"
        locs = [self.item(k, v) for k, v in frame.f_locals.items() if visible(k, v, module_scope)]
        scopes = [{"name": "locals" if not module_scope else "program", "items": locs}]
        if not module_scope:
            glob = [self.item(k, v) for k, v in frame.f_globals.items() if visible(k, v, True)]
            if glob:
                scopes.append({"name": "program", "items": glob})
        return scopes

    def expand(self, ref):
        v = self.refs.get(ref)
        if v is None:
            return []
        if isinstance(v, dict):
            return [self.item(short(k, 40), x) for k, x in list(v.items())[:200]]
        if isinstance(v, (list, tuple)):
            return [self.item(i, x) for i, x in enumerate(list(v)[:200])]
        if isinstance(v, (set, frozenset)):
            return [self.item(i, x) for i, x in enumerate(list(v)[:200])]
        return [self.item(k, x) for k, x in list(vars(v).items())[:200] if not k.startswith("__")]

    def evaluate(self, fid, expr):
        frame = self.frames.get(fid)
        if frame is None:
            return {"ok": False, "value": "not stopped"}
        try:
            v = eval(expr, frame.f_globals, frame.f_locals)
            return {"ok": True, **self.item(expr, v)}
        except Exception as e:
            return {"ok": False, "value": f"{type(e).__name__}: {e}"}

    # js> blocks: tag them with their Sym location so V8 reports positions we can map back,
    # and stop inside them when a step-in or a breakpoint asks for it
    def wrap_foreign(self, lang, code):
        if lang != "js":
            return code
        frame = sys._getframe(1)
        while frame is not None and not (is_user_code(frame.f_code) and "__SYM_LINEMAP__" in frame.f_globals):
            frame = frame.f_back
        file, line = ("block", 0)
        if frame is not None:
            file, line, _ = location(frame)
            line = line or 0
        url = f"symblock://{file}:{line}"
        nlines = code.count("\n") + 1
        self.js_blocks[url] = (file, line)
        self.js_len[url] = nlines
        bps = [b for b in self.breakpoints.get(file, ()) if line < b <= line + nlines]
        want = (self.step_foreign or bps or self.js_bps.get(url)) and self.ch.live and self.node_port
        head = "/*symblock*/"
        if want and self.ensure_node():
            self.py_stack = self.stack(frame, base=1000) if frame is not None else []
            self.sync_js_breakpoints(url, bps)
            if self.step_foreign:
                head += "debugger;"
        self.step_foreign = False
        return head + "\n" + code + f"\n//# sourceURL={url}\n"

    def ensure_node(self):
        if self.node is not None and self.node.sock is not None:
            return True
        self.node = NodeInspector(self.node_port, self.on_node)
        if not self.node.connect():
            self.node = None
            return False
        return True

    def sync_js_breakpoints(self, url, lines):
        for bid in self.js_bps.get(url, []):
            self.node.call("Debugger.removeBreakpoint", {"breakpointId": bid})
        ids = []
        _, first = self.js_blocks[url]
        for b in lines:
            r = self.node.call("Debugger.setBreakpointByUrl", {"url": url, "lineNumber": BLOCK_LINE0 + (b - first), "columnNumber": 0})
            if r.get("breakpointId"):
                ids.append(r["breakpointId"])
        self.js_bps[url] = ids

    def sync_file_breakpoints(self, f, lines):
        url = "file://" + f
        for bid in self.js_bps.get(url, []):
            self.node.call("Debugger.removeBreakpoint", {"breakpointId": bid})
        ids = []
        for b in lines:
            r = self.node.call("Debugger.setBreakpointByUrl", {"url": url, "lineNumber": b - 1, "columnNumber": 0})
            if r.get("breakpointId"):
                ids.append(r["breakpointId"])
        self.js_bps[url] = ids

    def js_frame_loc(self, cf):
        url = cf.get("url") or self.node.scripts.get(cf["location"]["scriptId"], "")
        ln = cf["location"]["lineNumber"]
        if url.startswith("symblock://"):
            file, first = self.js_blocks.get(url, (url[11:].rsplit(":", 1)[0], 0))
            k = ln - BLOCK_LINE0
            if k >= self.js_len.get(url, 10 ** 9) - 1:
                return None   # the closing brace the worker wraps around the block
            return file, first + max(k, 0), "js"
        if url.startswith("file://"):
            path = url[7:]
            if "node_modules" in path or "/bridge/workers/" in path:
                return None
            return path, ln + 1, "js"
        return None

    def on_node(self, method, p):
        if method == "Debugger.resumed":
            if self.js is not None:
                self.js = None
                self.ch.send({"event": "continued"})
            return
        if method != "Debugger.paused":
            return
        frames = []
        self.js_frames = {}
        top = (p.get("callFrames") or [{}])[0]
        top_url = top.get("url") or (self.node.scripts.get(top.get("location", {}).get("scriptId"), "") if top else "")
        if top_url.startswith("symblock://") and top["location"]["lineNumber"] <= BLOCK_LINE0:
            # the `debugger;` we planted: move on to the block's first real line
            self.js_stepping = True
            threading.Thread(target=self.node.call, args=("Debugger.stepOver",), daemon=True).start()
            return
        for cf in p.get("callFrames", []):
            loc = self.js_frame_loc(cf)
            if loc is None:
                continue
            fid = len(self.js_frames)
            self.js_frames[fid] = cf
            in_block = (cf.get("url") or self.node.scripts.get(cf["location"]["scriptId"], "")).startswith("symblock://")
            frames.append({"id": fid, "name": cf.get("functionName") or ("js> block" if in_block else "(top level)"), "file": loc[0], "line": loc[1], "lang": "js"})
        if not frames:
            # stepped out of the block into the worker's own code: let it finish, and stop on
            # the next Sym line if the user was stepping
            if self.js_stepping:
                self.mode = "in"
            self.js_stepping = False
            threading.Thread(target=self.node.call, args=("Debugger.resume",), daemon=True).start()
            return
        self.js = p
        self.js_refs = {}
        reason = "step" if self.js_stepping else ("breakpoint" if p.get("hitBreakpoints") else "step")
        self.js_stepping = False
        self.ch.send({"event": "stopped", "reason": reason, "frames": frames + self.py_stack})

    def js_item(self, name, ro):
        text, ty = js_value(ro)
        it = {"name": str(name), "value": text, "type": ty}
        if ro.get("objectId") and ro.get("type") == "object" and ro.get("subtype") != "null":
            ref = 100000 + len(self.js_refs) + 1
            self.js_refs[ref] = ro["objectId"]
            it["ref"] = ref
        return it

    def js_props(self, object_id):
        r = self.node.call("Runtime.getProperties", {"objectId": object_id, "ownProperties": True, "generatePreview": False})
        out = []
        for pr in r.get("result", [])[:300]:
            if "value" not in pr or pr["name"] == "__proto__":
                continue
            out.append(self.js_item(pr["name"], pr["value"]))
        return out

    def handle_js(self, msg):
        cmd = msg.get("cmd")
        if cmd == "setBreakpoints":
            f = os.path.abspath(msg.get("file", ""))
            lines = set(int(x) for x in msg.get("lines", []))
            self.breakpoints[f] = lines
            self.bp_version += 1
            if f.endswith((".js", ".mjs", ".cjs")) and self.node is not None:
                self.sync_file_breakpoints(f, lines)
            for url, (file, first) in list(self.js_blocks.items()):
                if file == f and self.node is not None:
                    self.sync_js_breakpoints(url, [b for b in lines if b > first])
            return
        if self.js is None:
            return
        if cmd == "continue":
            self.mode = "run"
            self.node.call("Debugger.resume")
        elif cmd == "next":
            self.js_stepping = True
            self.node.call("Debugger.stepOver")
        elif cmd == "stepIn":
            self.js_stepping = True
            self.node.call("Debugger.stepInto")
        elif cmd == "stepOut":
            self.js_stepping = True
            self.mode = "in"
            self.node.call("Debugger.stepOut")
        elif cmd == "variables":
            fid = msg.get("frame", 0)
            if fid >= 1000:
                self.ch.send({"event": "variables", "frame": fid, "scopes": self.variables(fid)})
                return
            cf = self.js_frames.get(fid)
            scopes = []
            for sc in (cf or {}).get("scopeChain", []):
                if sc["type"] in ("global",):
                    continue
                items = self.js_props(sc["object"]["objectId"])
                items = [i for i in items if i["name"] not in ("require", "sym") or sc["type"] != "local"]
                if items:
                    scopes.append({"name": {"local": "js locals", "block": "js block", "closure": "closure"}.get(sc["type"], sc["type"]), "items": items})
            # what the block shares with Sym
            for sc in (cf or {}).get("scopeChain", []):
                if sc["type"] == "local" and (cf.get("url") or self.node.scripts.get(cf["location"]["scriptId"], "")).startswith("symblock://"):
                    r = self.node.call("Debugger.evaluateOnCallFrame", {"callFrameId": cf["callFrameId"], "expression": "sym"})
                    if r.get("result", {}).get("objectId"):
                        shared = self.js_props(r["result"]["objectId"])
                        if shared:
                            scopes.append({"name": "shared with Sym", "items": shared})
                    break
            self.ch.send({"event": "variables", "frame": fid, "scopes": scopes})
        elif cmd == "expand":
            ref = msg.get("ref")
            if ref in self.js_refs:
                self.ch.send({"event": "expanded", "ref": ref, "items": self.js_props(self.js_refs[ref])})
            else:
                self.ch.send({"event": "expanded", "ref": ref, "items": self.expand(ref)})
        elif cmd == "evaluate":
            fid = msg.get("frame", 0)
            if fid >= 1000:
                self.ch.send({"event": "evaluated", "id": msg.get("id"), **self.evaluate(fid, msg.get("expr", ""))})
                return
            cf = self.js_frames.get(fid) or {}
            r = self.node.call("Debugger.evaluateOnCallFrame", {"callFrameId": cf.get("callFrameId", ""), "expression": msg.get("expr", "")})
            if r.get("exceptionDetails"):
                d = r["exceptionDetails"]
                text = (d.get("exception") or {}).get("description") or d.get("text", "error")
                self.ch.send({"event": "evaluated", "id": msg.get("id"), "ok": False, "value": text.split("\n")[0]})
            else:
                self.ch.send({"event": "evaluated", "id": msg.get("id"), "ok": True, **self.js_item(msg.get("expr", ""), r.get("result", {}))})
        elif cmd in ("stop", "disconnect"):
            os._exit(130)

    def run(self, path, argv):
        self.configure()
        sys.settrace(self.trace_call)
        threading.settrace(self.trace_call)
        try:
            run_program(path, argv)
        except SystemExit:
            raise
        except BaseException as e:
            tb = e.__traceback__
            last = None
            while tb is not None:
                if is_user_code(tb.tb_frame.f_code):
                    last = tb.tb_frame
                tb = tb.tb_next
            sys.settrace(None)
            if last is not None and self.ch.live:
                self.stop(last, "exception", {"type": type(e).__name__, "message": str(e)})
            raise
        finally:
            sys.settrace(None)


# ── trace mode: live values ──────────────────────────────────────────────
class Tracer:
    HIST = 8

    def __init__(self, ch):
        self.ch = ch
        self.lines = {}        # (file, line) → {"text", "hits", "hist"}
        self.pending = {}      # id(frame) → (file, line, snapshot)
        self.dirty = set()
        self.started = time.perf_counter()
        self.budget = float(os.environ.get("SYMDBG_BUDGET", "20"))
        self.off = False
        self.last_flush = 0.0
        self.calls = {}        # code → calls seen
        self.saturated = set() # (file, line) of functions called so often we stopped describing them

    CALL_LIMIT = 400

    def snapshot(self, frame):
        module_scope = frame.f_code.co_name == "<module>"
        out = {}
        for k, v in frame.f_locals.items():
            if visible(k, v, module_scope):
                out[k] = (id(v), short(v, 60))
        return out

    def record(self, key, text):
        e = self.lines.get(key)
        if e is None:
            e = self.lines[key] = {"text": "", "hits": 0, "hist": []}
        e["hits"] += 1
        if text:
            e["text"] = text
            if not e["hist"] or e["hist"][-1] != text:
                e["hist"].append(text)
                if len(e["hist"]) > self.HIST:
                    e["hist"].pop(0)
        self.dirty.add(key)

    def finish_line(self, frame, now_locals):
        prev = self.pending.get(id(frame))
        if prev is None:
            return
        file, line, before = prev
        changed = []
        for k, (i, s) in now_locals.items():
            b = before.get(k)
            if b is None or b[1] != s:
                changed.append(f"{k} = {s}")
        key = (file, line)
        e = self.lines.get(key)
        if e is not None and e["hits"] > 2000:
            e["hits"] += 1  # hot loop: count, stop describing
            self.dirty.add(key)
        else:
            self.record(key, ", ".join(changed[:4]))

    def trace_call(self, frame, event, arg):
        if self.off:
            return None
        if event == "call" and is_user_code(frame.f_code):
            code = frame.f_code
            if code.co_name != "<module>":
                n = self.calls.get(code, 0) + 1
                self.calls[code] = n
                if n > self.CALL_LIMIT:
                    # a hot function (fib's recursion): its lines have said what they do; stop
                    # tracing new calls so the program runs at near full speed
                    if n == self.CALL_LIMIT + 1:
                        file, _, _ = location(frame)
                        lm = frame.f_globals.get("__SYM_LINEMAP__")
                        for (_, _, ln) in code.co_lines():
                            sl = lm.get(ln) if lm is not None else ln
                            if sl:
                                self.saturated.add((file, sl))
                                self.dirty.add((file, sl))
                    return None
            return self.trace_local
        return None

    def trace_local(self, frame, event, arg):
        if self.off:
            return None
        if event == "line":
            file, line, lang = location(frame)
            if line is None:
                return self.trace_local
            prev = self.pending.get(id(frame))
            if prev is not None and prev[1] == line:
                return self.trace_local
            snap = self.snapshot(frame)
            self.finish_line(frame, snap)
            self.pending[id(frame)] = (file, line, snap)
            now = time.perf_counter()
            if now - self.last_flush > 0.25:
                self.flush()
                self.last_flush = now
                if now - self.started > self.budget:
                    self.off = True  # stop slowing the program down
                    self.ch.send({"event": "trace_budget", "seconds": self.budget})
        elif event == "return":
            prev = self.pending.pop(id(frame), None)
            if prev is not None:
                file, line, before = prev
                self.record((file, line), "← " + short(arg, 60) if arg is not None or frame.f_code.co_name != "<module>" else "")
        return self.trace_local

    def payload(self, keys=None):
        files = {}
        for (file, line), e in self.lines.items():
            if keys is not None and (file, line) not in keys:
                continue
            if (file, line) in self.saturated:
                e = dict(e, more=True)
            files.setdefault(file, {})[str(line)] = e
        return files

    def flush(self):
        if not self.dirty or not self.ch.live:
            return
        keys = self.dirty
        self.dirty = set()
        self.ch.send({"event": "values", "files": self.payload(keys)})

    def run(self, path, argv):
        sys.settrace(self.trace_call)
        threading.settrace(self.trace_call)
        try:
            run_program(path, argv)
        finally:
            sys.settrace(None)
            self.flush()
            if OUT:
                with open(OUT, "w") as f:
                    json.dump({"files": self.payload()}, f)


# ── profile mode: time per function ──────────────────────────────────────
class Profiler:
    """A sampling profiler: every millisecond, look at what the program is doing. Cheap enough
    that the numbers mean something (a tracing profiler makes recursive Python 30× slower)."""
    INTERVAL = 0.001

    def __init__(self, ch):
        self.ch = ch
        self.funcs = {}     # "file::name" → samples (inclusive)
        self.lines = {}     # "file:line" → samples where the program was on that line (self time)
        self.defs = {}      # "file::name" → def line
        self.samples = 0
        self.done = threading.Event()

    def sample(self, main_id):
        while not self.done.is_set():
            time.sleep(self.INTERVAL)
            frame = sys._current_frames().get(main_id)
            if frame is None:
                continue
            self.samples += 1
            seen = set()
            top = True
            while frame is not None:
                if is_user_code(frame.f_code):
                    file, line, lang = location(frame)
                    if top and line:
                        k = f"{file}:{line}"
                        self.lines[k] = self.lines.get(k, 0) + 1
                        top = False
                    name = frame.f_code.co_name
                    if name != "<module>":
                        key = f"{file}::{name}"
                        if key not in seen:
                            seen.add(key)
                            self.funcs[key] = self.funcs.get(key, 0) + 1
                            if key not in self.defs:
                                self.defs[key] = def_line(file, name) if file.endswith(".sym") else frame.f_code.co_firstlineno
                frame = frame.f_back

    def run(self, path, argv):
        sys.setswitchinterval(0.0005)
        t = threading.Thread(target=self.sample, args=(threading.get_ident(),), daemon=True)
        t0 = time.perf_counter()
        t.start()
        try:
            run_program(path, argv)
        finally:
            total = time.perf_counter() - t0
            self.done.set()
            t.join(0.5)
            per = total / max(self.samples, 1)
            res = {"total": total, "samples": self.samples,
                   "functions": {k: {"seconds": n * per, "share": n / max(self.samples, 1), "line": self.defs.get(k, 0)} for k, n in self.funcs.items()},
                   "lines": {k: n * per for k, n in self.lines.items()}}
            self.ch.send({"event": "profile", **res})
            if OUT:
                with open(OUT, "w") as f:
                    json.dump(res, f)


def debug_node_program(dbg, path, argv):
    """A plain .js file: Node runs it under its inspector, and the same protocol reaches the editor."""
    import subprocess
    port = dbg.node_port
    proc = subprocess.Popen(["node", f"--inspect-brk=127.0.0.1:{port}", path, *argv], stderr=subprocess.PIPE, text=True)

    def errs():
        for line in proc.stderr:
            if not line.startswith(("Debugger listening on", "For help, see:", "Debugger attached.", "Waiting for the debugger", "Debugger ending on")):
                sys.stderr.write(line)
    threading.Thread(target=errs, daemon=True).start()
    url = "file://" + os.path.abspath(path)
    started = threading.Event()
    orig = dbg.on_node

    def on_node(method, p):
        if method == "Debugger.paused" and not started.is_set():
            started.set()       # the --inspect-brk stop before line 1: set breakpoints, then go
            return
        if method == "Runtime.executionContextDestroyed":
            try:
                dbg.node.sock.close()
            except Exception:
                pass
            return
        orig(method, p)
    dbg.node = NodeInspector(port, on_node)
    if not dbg.node.connect():
        proc.kill()
        raise RuntimeError("couldn't attach to node")
    started.wait(5)
    dbg.configure()
    for f, lines in list(dbg.breakpoints.items()):
        dbg.sync_file_breakpoints(f, lines)
    dbg.node.call("Debugger.resume")
    return proc.wait()


def run_program(path, argv):
    sys.argv = [path] + list(argv)
    d = os.path.dirname(os.path.abspath(path))
    if d not in sys.path:
        sys.path.insert(0, d)
    runpy.run_path(path, run_name="__main__")


CH = None
DBG = None


def main():
    global CH, DBG
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    path, argv = sys.argv[1], sys.argv[2:]
    CH = Channel(ADDR)
    code = 0
    try:
        if MODE == "trace":
            DBG = Tracer(CH)
        elif MODE == "profile":
            DBG = Profiler(CH)
        else:
            if not os.environ.get("SYMDBG_NODE_PORT"):
                # js> blocks will start Node with its inspector on this port (see bridge/broker.py)
                s = socket.socket()
                s.bind(("127.0.0.1", 0))
                os.environ["SYMDBG_NODE_PORT"] = str(s.getsockname()[1])
                s.close()
            DBG = Debugger(CH)
        sys.modules.setdefault("symdbg", sys.modules[__name__])
        if MODE == "debug" and path.endswith((".js", ".mjs", ".cjs")):
            code = debug_node_program(DBG, path, argv)
        else:
            DBG.run(path, argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        traceback.print_exc()
        code = 1
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        CH.send({"event": "exited", "code": code})
        time.sleep(0.05)
    sys.exit(code)


if __name__ == "__main__":
    main()

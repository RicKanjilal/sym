"""
Sym Runtime Builtins
These functions are available to all Sym programs.
Includes smart execution (adaptive algorithms).
"""

import time
import copy
import sys
import os
import functools
from typing import Any, List, Callable

__all__ = [
    '_sym_smart_sort', '_sym_clone', '_sym_benchmark', '_format_time',
    '_is_nearly_sorted', '_insertion_sort',
    'sqrt', 'sin', 'cos', 'tan', 'log', 'log2', 'log10',
    'abs_val', 'floor', 'ceil', 'round_val', 'PI', 'E', 'INF',
    'split', 'join', 'strip', 'upper', 'lower', 'replace',
    'starts_with', 'ends_with', 'contains',
    'read_file', 'write_file', 'append_file', 'exists', 'input_line',
    'serve', 'page_with_js', '__rawb64', 'serve_realtime', 'ws_broadcast', 'ui_page',
    '__java_import', '__js_import', '__bridge_exec', 'sym_set', 'sym_get',
    '__php_import', '__ruby_import', '__r_import', '__perl_import', '__c_import', '__sym_import',
]


# ═══════════════════════════════════════════════════════════════
# SMART SORT — Adaptive Algorithm Selection
# ═══════════════════════════════════════════════════════════════

def _is_nearly_sorted(data: list, threshold: float = 0.1) -> bool:
    """Check if list is nearly sorted (< threshold fraction out of order)"""
    if len(data) < 2:
        return True
    inversions = sum(1 for i in range(len(data) - 1) if data[i] > data[i+1])
    return inversions / len(data) < threshold


def _insertion_sort(data: list) -> list:
    """Best for small arrays (< 16 elements)"""
    for i in range(1, len(data)):
        key = data[i]
        j = i - 1
        while j >= 0 and data[j] > key:
            data[j + 1] = data[j]
            j -= 1
        data[j + 1] = key
    return data


def _sym_smart_sort(data: Any) -> Any:
    """
    Smart sort: picks the best algorithm based on data characteristics.
    Sorts IN-PLACE and returns the sorted data.
    """
    if not isinstance(data, list):
        # Might be a numpy array or other sortable
        if hasattr(data, 'sort'):
            data.sort()
            return data
        return sorted(data)

    n = len(data)

    if n < 2:
        return data

    # Tiny: insertion sort (minimal overhead)
    if n < 16:
        return _insertion_sort(data)

    # Nearly sorted: Python's Timsort is optimal
    if _is_nearly_sorted(data):
        data.sort()
        return data

    # Medium/Large: use Python's Timsort (already great for general case)
    # For truly massive data (> 1M), we'd use parallel sort in Phase 2
    data.sort()
    return data


# ═══════════════════════════════════════════════════════════════
# CLONE — Deep Copy
# ═══════════════════════════════════════════════════════════════

def _sym_clone(obj: Any) -> Any:
    """Deep clone any object"""
    return copy.deepcopy(obj)


# ═══════════════════════════════════════════════════════════════
# BENCHMARK — Timing Wrapper
# ═══════════════════════════════════════════════════════════════

def _sym_benchmark(func: Callable) -> Callable:
    """Decorator that benchmarks a function across multiple input sizes"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        sizes = [100, 1_000, 10_000, 100_000, 1_000_000]
        print(f"\n{'─' * 52}")
        print(f" Benchmark: {func.__name__}")
        print(f"{'─' * 52}")
        print(f" {'Input Size':<14} {'Time':<12} {'Throughput':<16}")
        print(f"{'─' * 52}")

        # Run the function normally first
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start

        # Show result
        print(f" {'(actual)':<14} {_format_time(elapsed):<12}")
        print(f"{'─' * 52}\n")
        return result

    return wrapper


def _format_time(seconds: float) -> str:
    if seconds < 1e-6:
        return f"{seconds * 1e9:.1f} ns"
    if seconds < 1e-3:
        return f"{seconds * 1e6:.1f} μs"
    if seconds < 1:
        return f"{seconds * 1e3:.1f} ms"
    return f"{seconds:.2f} s"


# ═══════════════════════════════════════════════════════════════
# MATH BUILTINS
# ═══════════════════════════════════════════════════════════════

import math

def sqrt(x):
    return math.sqrt(x)

def sin(x):
    return math.sin(x)

def cos(x):
    return math.cos(x)

def tan(x):
    return math.tan(x)

def log(x, base=None):
    if base:
        return math.log(x, base)
    return math.log(x)

def log2(x):
    return math.log2(x)

def log10(x):
    return math.log10(x)

def abs_val(x):
    return abs(x)

def floor(x):
    return math.floor(x)

def ceil(x):
    return math.ceil(x)

def round_val(x, n=0):
    return round(x, n)

PI = math.pi
E = math.e
INF = math.inf


# ═══════════════════════════════════════════════════════════════
# STRING BUILTINS
# ═══════════════════════════════════════════════════════════════

def split(s, sep=None):
    return s.split(sep)

def join(sep, lst):
    return sep.join(str(x) for x in lst)

def strip(s):
    return s.strip()

def upper(s):
    return s.upper()

def lower(s):
    return s.lower()

def replace(s, old, new):
    return s.replace(old, new)

def starts_with(s, prefix):
    return s.startswith(prefix)

def ends_with(s, suffix):
    return s.endswith(suffix)

def contains(s, sub):
    return sub in s


# ═══════════════════════════════════════════════════════════════
# IO BUILTINS
# ═══════════════════════════════════════════════════════════════

def read_file(path: str) -> str:
    with open(path, "r") as f:
        return f.read()

def write_file(path: str, content: str):
    with open(path, "w") as f:
        f.write(content)

def append_file(path: str, content: str):
    with open(path, "a") as f:
        f.write(content)

def exists(path: str) -> bool:
    return os.path.exists(path)

def input_line(prompt: str = "") -> str:
    return input(prompt)


# ═══════════════════════════════════════════════════════════════
# WEB — one-file full-stack. serve() turns a .sym into a web server.
# Frontend .sym compiles to JS and is injected into the page automatically.
# ═══════════════════════════════════════════════════════════════
import json as _json
from http.server import BaseHTTPRequestHandler as _BH, HTTPServer as _HS

def serve(config):
    """serve({"port":5000, "routes":{"/api": handler_fn}, "page": html_string})
    handler_fn(method, body_dict) -> dict/list/str. GET / returns page."""
    port = config.get("port", 5000)
    routes = config.get("routes", {})
    page = config.get("page", "<h1>Sym</h1>")

    class _H(_BH):
        def log_message(self, *a): pass
        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if isinstance(body, (dict, list)):
                body = _json.dumps(body)
            self.wfile.write(str(body).encode())
        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            try: return _json.loads(raw or b"{}")
            except Exception: return {}
        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self._send(200, page, "text/html")
            elif self.path in routes:
                self._send(200, routes[self.path]("GET", {}))
            else:
                self._send(404, {"error": "not found"})
        def do_POST(self):
            if self.path in routes:
                self._send(200, routes[self.path]("POST", self._body()))
            else:
                self._send(404, {"error": "not found"})
        def do_OPTIONS(self):
            self._send(200, {})

    print(f"  🔥 Sym server on http://localhost:{port}")
    _HS(("0.0.0.0", port), _H).serve_forever()


def page_with_js(html_body, js_code):
    """Wrap HTML + compiled JS into one served page string."""
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{html_body}<script>{js_code}</script></body></html>"

import base64 as _b64_mod
def __rawb64(s):
    """Decode a base64 raw block (used by frontend{}/html{}/css{} preprocessor)."""
    return _b64_mod.b64decode(s).decode()

def ui_page(ui_html="", css="", js=""):
    """Combine compiled __ui + __css (+ optional js) into one served page.
    __ui already is a full HTML doc; inject css into its <style> and js before </body>."""
    html = ui_html or "<!DOCTYPE html><html><body></body></html>"
    if css:
        html = html.replace("</style>", css + "</style>", 1)
    if js:
        html = html.replace("</body>", "<script>" + js + "</script></body>", 1)
    return html


# ═══════════════════════════════════════════════════════════════
# WEBSOCKETS — pure stdlib, no deps. serve_realtime() gives HTTP + WS.
# ═══════════════════════════════════════════════════════════════
import socket as _sock, threading as _thr, hashlib as _hl, base64 as _b64x, struct as _st

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def _ws_handshake(conn, headers):
    key = ""
    for line in headers.split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    accept = _b64x.b64encode(_hl.sha1((key + _WS_MAGIC).encode()).digest()).decode()
    conn.send(("HTTP/1.1 101 Switching Protocols\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())

def _ws_recv(conn):
    try:
        d = conn.recv(2)
        if len(d) < 2: return None
        length = d[1] & 127
        if length == 126: length = _st.unpack(">H", conn.recv(2))[0]
        elif length == 127: length = _st.unpack(">Q", conn.recv(8))[0]
        mask = conn.recv(4)
        data = bytearray(conn.recv(length))
        for i in range(length): data[i] ^= mask[i % 4]
        return data.decode(errors="ignore")
    except Exception:
        return None

def _ws_send(conn, msg):
    b = msg.encode()
    hdr = bytearray([0x81])
    n = len(b)
    if n < 126: hdr.append(n)
    elif n < 65536: hdr += bytes([126]) + _st.pack(">H", n)
    else: hdr += bytes([127]) + _st.pack(">Q", n)
    try: conn.send(bytes(hdr) + b)
    except Exception: pass

_ws_clients = []

def ws_broadcast(msg):
    """Send a message to every connected websocket client."""
    for c in list(_ws_clients):
        _ws_send(c, msg)

def serve_realtime(config):
    """serve_realtime({"port":5000,"page":html,"on_message":fn})
    fn(msg_str) is called per incoming WS message; return a str to broadcast,
    or call ws_broadcast() yourself. HTTP GET / serves the page."""
    port = config.get("port", 5000)
    page = config.get("page", "<h1>Sym</h1>")
    on_message = config.get("on_message")

    srv = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    srv.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port)); srv.listen(50)
    print(f"  🔥 Sym realtime server on http://localhost:{port}")

    def client(conn):
        req = conn.recv(2048).decode(errors="ignore")
        if "upgrade: websocket" in req.lower():
            _ws_handshake(conn, req)
            _ws_clients.append(conn)
            while True:
                m = _ws_recv(conn)
                if m is None: break
                if on_message:
                    out = on_message(m)
                    if isinstance(out, str): ws_broadcast(out)
            if conn in _ws_clients: _ws_clients.remove(conn)
            conn.close()
        else:
            body = page.encode()
            conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                      b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            conn.close()

    while True:
        c, _ = srv.accept()
        _thr.Thread(target=client, args=(c,), daemon=True).start()


# ═══════════════════════════════════════════════════════════════
# SYMBRIDGE — Polyglot runtime hosting (Sym is the host)
# java.import / js.import / java> {} / js> {} / sym_get / sym_set
# ═══════════════════════════════════════════════════════════════

def _get_bridge():
    from bridge.broker import get_bridge
    return get_bridge()

def __java_import(target):
    """java.import math.Calculator — Sym hosts the JVM, brokers Symbol Objects."""
    return _get_bridge().import_module("java", target)

def __js_import(target):
    """js.import lodash — Sym hosts Node, brokers Symbol Objects."""
    return _get_bridge().import_module("js", target)

def __bridge_exec(lang, b64_code):
    """Run a  java> {} / js> {} / python> {}  block in its real runtime."""
    import base64
    code = base64.b64decode(b64_code).decode()
    if lang == "python":
        # python> blocks run in the host runtime itself, sharing `sym`
        b = _get_bridge()
        exec(code, {"sym": b.shared})
        return None
    return _get_bridge().exec_block(lang, code)

def sym_set(key, value):
    """Put a value into the shared Symbol Object space (visible to all blocks)."""
    _get_bridge().shared[key] = value
    return value

def sym_get(key, default=None):
    """Read a value from the shared Symbol Object space."""
    return _get_bridge().shared.get(key, default)

def __php_import(target):   return _get_bridge().import_module("php", target)
def __ruby_import(target):  return _get_bridge().import_module("ruby", target)
def __r_import(target):     return _get_bridge().import_module("r", target)
def __perl_import(target):  return _get_bridge().import_module("perl", target)

def __c_import(target):
    """c.import sqlite3 — native shared library via FFI (the OS loader IS the bridge)."""
    from bridge.extras import c_import
    return c_import(target)

def __sym_import(target):
    """sym.import tensorflow — the registry routes any name to its home ecosystem."""
    from bridge.extras import sym_import
    return sym_import(target, _get_bridge)

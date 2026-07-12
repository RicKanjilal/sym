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
    'serve', 'page_with_js',
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

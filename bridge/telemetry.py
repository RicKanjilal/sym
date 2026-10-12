"""
Bridge telemetry — what the broker is doing, as it does it.

Off unless $SYM_TELEMETRY names a Unix socket (editors such as Velnor set it
for every terminal they own). Events are newline-delimited JSON, sent from a
background thread so a slow or vanished listener never slows a call:

  {"ev": "hello",        "pid", "argv", "t"}
  {"ev": "worker_start", "lang", "worker_pid", "t"}
  {"ev": "worker_exit",  "lang", "t"}
  {"ev": "call",         "lang", "op", "target", "ms", "ok", "src", "t"}   src = "file.sym:line"
  {"ev": "handle_new",   "lang", "id", "type", "t"}
  {"ev": "handle_free",  "lang", "id", "t"}
  {"ev": "shared",       "keys", "t"}
"""

import json
import os
import queue
import socket
import sys
import threading
import time

_PATH = os.environ.get("SYM_TELEMETRY")
_q = None
_dead = False


def _pump():
    global _dead
    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(_PATH)
        sock.settimeout(None)
    except OSError:
        _dead = True
        return
    while True:
        item = _q.get()
        if item is None:
            break
        try:
            sock.sendall(item)
        except OSError:
            _dead = True
            break
    try:
        sock.close()
    except OSError:
        pass


def enabled() -> bool:
    return bool(_PATH) and not _dead


def caller():
    """The Sym line (file:line) that caused this crossing — so an editor can jump to it."""
    f = sys._getframe(2)
    for _ in range(14):
        if f is None:
            return None
        lm = f.f_globals.get("__SYM_LINEMAP__")
        if lm is not None and f.f_lineno in lm:
            src = f.f_globals.get("__SYM_SRC__") or ""
            d = os.path.dirname(os.path.abspath(f.f_code.co_filename))
            return f"{os.path.join(d, os.path.basename(src))}:{lm[f.f_lineno]}"
        f = f.f_back
    return None


def emit(ev: str, **fields):
    global _q
    if not _PATH or _dead:
        return
    if ev == "call" and "src" not in fields:
        try:
            fields["src"] = caller()
        except Exception:
            pass
    if _q is None:
        _q = queue.Queue(maxsize=10000)
        threading.Thread(target=_pump, name="sym-telemetry", daemon=True).start()
        _put({"ev": "hello", "pid": os.getpid(), "argv": sys.argv[:4], "t": time.time()})
    fields["ev"] = ev
    fields["pid"] = os.getpid()
    fields["t"] = time.time()
    _put(fields)


def _put(obj):
    try:
        _q.put_nowait((json.dumps(obj, default=str) + "\n").encode())
    except queue.Full:
        pass

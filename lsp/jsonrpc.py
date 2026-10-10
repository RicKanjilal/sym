"""Minimal JSON-RPC 2.0 over stdio with LSP Content-Length framing. No deps."""

import json
import sys
import threading


class Transport:
    def __init__(self, inp=None, out=None):
        self.inp = inp or sys.stdin.buffer
        self.out = out or sys.stdout.buffer
        self._wlock = threading.Lock()

    def read(self):
        headers = {}
        while True:
            line = self.inp.readline()
            if not line:
                return None
            line = line.decode("ascii", "replace").strip()
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        body = b""
        while len(body) < n:
            chunk = self.inp.read(n - len(body))
            if not chunk:
                return None
            body += chunk
        return json.loads(body.decode("utf-8"))

    def write(self, msg):
        data = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with self._wlock:
            self.out.write(b"Content-Length: %d\r\n\r\n" % len(data))
            self.out.write(data)
            self.out.flush()

    def respond(self, id_, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": id_}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self.write(msg)

    def notify(self, method, params):
        self.write({"jsonrpc": "2.0", "method": method, "params": params})


class LspError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

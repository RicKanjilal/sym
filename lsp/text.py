"""Documents, positions, and the UTF-16 ↔ code-point dance.

LSP positions count UTF-16 code units. Python strings count code points.
Symbol-mode files are full of astral glyphs (𝔹 𝕊 𝕃 𝔻 are two UTF-16 units
each), so every position crossing the wire is converted here and nowhere else.
"""


def cp_to_u16(line: str, col: int) -> int:
    n = 0
    for ch in line[:col]:
        n += 2 if ord(ch) > 0xFFFF else 1
    return n


def u16_to_cp(line: str, u16: int) -> int:
    n = 0
    for i, ch in enumerate(line):
        if n >= u16:
            return i
        n += 2 if ord(ch) > 0xFFFF else 1
    return len(line)


class Document:
    def __init__(self, uri, text, version=0, language_id="sym"):
        self.uri = uri
        self.version = version
        self.language_id = language_id
        self._set(text)

    def _set(self, text):
        self.text = text
        self.lines = text.split("\n")
        self._line_starts = None
        self._analysis = None

    @property
    def line_starts(self):
        if self._line_starts is None:
            starts, pos = [], 0
            for l in self.lines:
                starts.append(pos)
                pos += len(l) + 1
            self._line_starts = starts
        return self._line_starts

    # positions ---------------------------------------------------------
    def offset_at(self, pos) -> int:
        line = min(max(pos["line"], 0), len(self.lines) - 1)
        return self.line_starts[line] + u16_to_cp(self.lines[line], pos["character"])

    def position_at(self, offset: int):
        offset = max(0, min(offset, len(self.text)))
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        col = offset - self.line_starts[lo]
        return {"line": lo, "character": cp_to_u16(self.lines[lo], col)}

    def pos(self, line1: int, col1: int):
        """1-based line, 1-based code-point column → LSP position."""
        line = max(0, min(line1 - 1, len(self.lines) - 1))
        return {"line": line, "character": cp_to_u16(self.lines[line], max(0, col1 - 1))}

    def range(self, line1, col1, line2, col2):
        return {"start": self.pos(line1, col1), "end": self.pos(line2, col2)}

    def line_range(self, line1):
        line = max(0, min(line1 - 1, len(self.lines) - 1))
        text = self.lines[line]
        indent = len(text) - len(text.lstrip())
        return {"start": {"line": line, "character": cp_to_u16(text, indent)},
                "end": {"line": line, "character": cp_to_u16(text, len(text))}}

    def offset_range(self, start, end):
        return {"start": self.position_at(start), "end": self.position_at(end)}

    # edits ---------------------------------------------------------------
    def apply_changes(self, changes, version):
        text = self.text
        for ch in changes:
            if "range" not in ch or ch["range"] is None:
                text = ch["text"]
            else:
                self._set(text)
                s = self.offset_at(ch["range"]["start"])
                e = self.offset_at(ch["range"]["end"])
                text = text[:s] + ch["text"] + text[e:]
        self._set(text)
        self.version = version

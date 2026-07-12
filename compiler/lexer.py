"""
Sym Lexer
Tokenizes .sym source files in Normal, Compact, and Symbol modes.
Handles indentation-based scoping (like Python).
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import List, Optional


class TT(Enum):
    """Token Types"""
    # Literals
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()
    TRUE = auto()
    FALSE = auto()
    NONE = auto()

    # Identifiers
    IDENT = auto()

    # Keywords (unified across all modes)
    FN = auto()
    LET = auto()
    CONST = auto()
    RETURN = auto()
    IF = auto()
    ELIF = auto()
    ELSE = auto()
    FOR = auto()
    WHILE = auto()
    IN = auto()
    IMPORT = auto()
    FROM = auto()
    AS = auto()
    STRUCT = auto()
    TRAIT = auto()
    IMPL = auto()
    MATCH = auto()
    TRY = auto()
    CATCH = auto()
    BREAK = auto()
    CONTINUE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    PRINT = auto()
    SORT = auto()
    ASYNC = auto()
    AWAIT = auto()
    OPTIMIZE = auto()
    BENCHMARK = auto()
    SELF = auto()

    # Operators
    PLUS = auto()       # +
    MINUS = auto()      # -
    STAR = auto()       # *
    SLASH = auto()      # /
    DSLASH = auto()     # //
    PERCENT = auto()    # %
    POWER = auto()      # **
    EQ = auto()         # ==
    NEQ = auto()        # !=
    LT = auto()         # <
    GT = auto()         # >
    LTE = auto()        # <=
    GTE = auto()        # >=
    ASSIGN = auto()     # =
    PLUS_EQ = auto()    # +=
    MINUS_EQ = auto()   # -=
    STAR_EQ = auto()    # *=
    SLASH_EQ = auto()   # /=
    ARROW = auto()      # ->
    FAT_ARROW = auto()  # =>
    PIPE = auto()       # |>
    DOT = auto()        # .
    DOTDOT = auto()     # ..
    COLON = auto()      # :
    DCOLON = auto()     # ::
    COMMA = auto()      # ,
    AT = auto()         # @
    HASH = auto()       # #
    QUESTION = auto()   # ?
    AMP = auto()        # &
    TILDE = auto()      # ~
    UNDERSCORE = auto() # _

    # Delimiters
    LPAREN = auto()     # (
    RPAREN = auto()     # )
    LBRACKET = auto()   # [
    RBRACKET = auto()   # ]
    LBRACE = auto()     # {
    RBRACE = auto()     # }

    # Indentation
    INDENT = auto()
    DEDENT = auto()
    NEWLINE = auto()

    # Special
    EOF = auto()
    DIRECTIVE = auto()  # #compact, #symbol, etc.


@dataclass
class Token:
    type: TT
    value: str
    line: int
    col: int

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, L{self.line})"


# ── Keyword Maps ──────────────────────────────────────────────

NORMAL_KEYWORDS = {
    "fn": TT.FN, "let": TT.LET, "const": TT.CONST, "return": TT.RETURN,
    "if": TT.IF, "elif": TT.ELIF, "else": TT.ELSE, "for": TT.FOR,
    "while": TT.WHILE, "in": TT.IN, "import": TT.IMPORT, "from": TT.FROM,
    "as": TT.AS, "struct": TT.STRUCT, "trait": TT.TRAIT, "impl": TT.IMPL,
    "match": TT.MATCH, "try": TT.TRY, "catch": TT.CATCH,
    "break": TT.BREAK, "continue": TT.CONTINUE,
    "and": TT.AND, "or": TT.OR, "not": TT.NOT,
    "print": TT.PRINT, "sort": TT.SORT,
    "optimize": TT.OPTIMIZE, "benchmark": TT.BENCHMARK,
    "true": TT.TRUE, "false": TT.FALSE, "none": TT.NONE,
    "True": TT.TRUE, "False": TT.FALSE, "None": TT.NONE,
    "self": TT.SELF,
}

COMPACT_KEYWORDS = {
    "f": TT.FN, "@": TT.LET, "const": TT.CONST, "<-": TT.RETURN,
    "?": TT.IF, "??": TT.ELIF, "!": TT.ELSE, "~": TT.FOR,
    "~~": TT.WHILE, "in": TT.IN, "<<": TT.IMPORT, "from": TT.FROM,
    "as": TT.AS, "struct": TT.STRUCT, "trait": TT.TRAIT, "impl": TT.IMPL,
    "match": TT.MATCH, "try": TT.TRY, "catch": TT.CATCH,
    "break": TT.BREAK, "continue": TT.CONTINUE,
    "and": TT.AND, "or": TT.OR, "not": TT.NOT,
    ">>": TT.PRINT, "sort": TT.SORT,
    "optimize": TT.OPTIMIZE, "benchmark": TT.BENCHMARK,
    "true": TT.TRUE, "false": TT.FALSE, "none": TT.NONE,
    "self": TT.SELF,
}

# Symbol mode uses Unicode — handled specially in the lexer
SYMBOL_MAP = {
    "ƒ": TT.FN, "∂": TT.LET, "←": TT.RETURN, "→": TT.ARROW,
    "≤": TT.LTE, "≥": TT.GTE, "≠": TT.NEQ, "≔": TT.ASSIGN,
    "∀": TT.FOR, "⟳": TT.WHILE, "⊂": TT.IMPORT, "⊕": TT.PRINT,
    "ℤ": "int", "ℝ": "float", "𝔹": "bool", "𝕊": "str",
}


class Lexer:
    def __init__(self, source: str, filename: str = "<stdin>"):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: List[Token] = []
        self.indent_stack = [0]
        self.mode = "normal"

    def error(self, msg: str):
        raise SyntaxError(f"{self.filename}:{self.line}:{self.col}: {msg}")

    def peek(self, offset=0) -> str:
        p = self.pos + offset
        return self.source[p] if p < len(self.source) else "\0"

    def advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def match(self, expected: str) -> bool:
        if self.pos < len(self.source) and self.source[self.pos] == expected:
            self.advance()
            return True
        return False

    def add(self, tt: TT, value: str):
        self.tokens.append(Token(tt, value, self.line, self.col))

    def tokenize(self) -> List[Token]:
        # Detect mode from first line
        self._detect_mode()

        while self.pos < len(self.source):
            self._skip_blank_lines()
            if self.pos >= len(self.source):
                break
            self._handle_line()

        # Close remaining indents
        while len(self.indent_stack) > 1:
            self.indent_stack.pop()
            self.add(TT.DEDENT, "")

        self.add(TT.EOF, "")
        return self.tokens

    def _detect_mode(self):
        """Check first line for #compact or #symbol directive"""
        first_line = ""
        i = 0
        while i < len(self.source) and self.source[i] != "\n":
            first_line += self.source[i]
            i += 1
        first_line = first_line.strip()
        if first_line == "#compact":
            self.mode = "compact"
            self.pos = i + 1 if i < len(self.source) else i
            self.line = 2
            self.col = 1
            self.add(TT.DIRECTIVE, "compact")
        elif first_line == "#symbol":
            self.mode = "symbol"
            self.pos = i + 1 if i < len(self.source) else i
            self.line = 2
            self.col = 1
            self.add(TT.DIRECTIVE, "symbol")

    def _skip_blank_lines(self):
        """Skip completely empty lines and comment-only lines"""
        while self.pos < len(self.source):
            save = self.pos
            # Count spaces on this line
            while self.pos < len(self.source) and self.source[self.pos] == " ":
                self.pos += 1
            # Check if rest of line is empty or comment
            if self.pos >= len(self.source):
                break
            if self.source[self.pos] == "\n":
                self.pos += 1
                self.line += 1
                self.col = 1
                continue
            if self.source[self.pos] == "#" and not self._is_directive():
                # Skip comment line
                while self.pos < len(self.source) and self.source[self.pos] != "\n":
                    self.pos += 1
                if self.pos < len(self.source):
                    self.pos += 1
                    self.line += 1
                    self.col = 1
                continue
            # Real content — restore position to re-measure indent
            self.pos = save
            break

    def _is_directive(self):
        """Check if current # is a directive like #compact"""
        if self.col != 1:
            return False
        save = self.pos
        if self.source[self.pos] == "#":
            word = ""
            i = self.pos + 1
            while i < len(self.source) and self.source[i].isalpha():
                word += self.source[i]
                i += 1
            return word in ("compact", "symbol", "manual_memory", "gpu")
        return False

    def _handle_line(self):
        """Process one logical line: indentation then tokens"""
        # Measure indentation
        indent = 0
        while self.pos < len(self.source) and self.source[self.pos] == " ":
            indent += 1
            self.advance()

        if self.pos >= len(self.source) or self.source[self.pos] == "\n":
            return

        # Emit indent/dedent tokens
        current = self.indent_stack[-1]
        if indent > current:
            self.indent_stack.append(indent)
            self.add(TT.INDENT, "")
        elif indent < current:
            while len(self.indent_stack) > 1 and self.indent_stack[-1] > indent:
                self.indent_stack.pop()
                self.add(TT.DEDENT, "")
            if self.indent_stack[-1] != indent:
                self.error(f"Inconsistent indentation: expected {self.indent_stack[-1]}, got {indent}")

        # Tokenize rest of line
        while self.pos < len(self.source) and self.source[self.pos] != "\n":
            self._next_token()

        # Consume newline
        if self.pos < len(self.source) and self.source[self.pos] == "\n":
            self.add(TT.NEWLINE, "\\n")
            self.advance()

    def _next_token(self):
        """Read the next token from current position"""
        # Skip spaces (not newlines)
        while self.pos < len(self.source) and self.source[self.pos] == " ":
            self.advance()

        if self.pos >= len(self.source) or self.source[self.pos] == "\n":
            return

        ch = self.source[self.pos]

        # Comments
        if ch == "#":
            # Check for directive
            if self._is_directive():
                self.advance()  # skip #
                word = ""
                while self.pos < len(self.source) and self.source[self.pos].isalnum():
                    word += self.advance()
                self.add(TT.DIRECTIVE, word)
                return
            # Regular comment — skip rest of line
            while self.pos < len(self.source) and self.source[self.pos] != "\n":
                self.advance()
            return

        # Symbol mode Unicode
        if self.mode == "symbol" and ch in SYMBOL_MAP:
            self.advance()
            val = SYMBOL_MAP[ch]
            if isinstance(val, TT):
                self.add(val, ch)
            else:
                self.add(TT.IDENT, val)  # type names like ℤ → "int"
            return

        # Strings
        if ch in ('"', "'"):
            self._read_string(ch)
            return

        # Numbers
        if ch.isdigit() or (ch == "." and self.peek(1).isdigit()):
            self._read_number()
            return

        # Identifiers and keywords
        if ch.isalpha() or ch == "_":
            self._read_identifier()
            return

        # Two-character operators (check before single)
        two = self.source[self.pos:self.pos+2] if self.pos + 1 < len(self.source) else ""
        if two == "**":
            self.advance(); self.advance(); self.add(TT.POWER, "**"); return
        if two == "//":
            self.advance(); self.advance(); self.add(TT.DSLASH, "//"); return
        if two == "==":
            self.advance(); self.advance(); self.add(TT.EQ, "=="); return
        if two == "!=":
            self.advance(); self.advance(); self.add(TT.NEQ, "!="); return
        if two == "<=":
            self.advance(); self.advance(); self.add(TT.LTE, "<="); return
        if two == ">=":
            self.advance(); self.advance(); self.add(TT.GTE, ">="); return
        if two == "->":
            self.advance(); self.advance(); self.add(TT.ARROW, "->"); return
        if two == "=>":
            self.advance(); self.advance(); self.add(TT.FAT_ARROW, "=>"); return
        if two == "|>":
            self.advance(); self.advance(); self.add(TT.PIPE, "|>"); return
        if two == "..":
            self.advance(); self.advance(); self.add(TT.DOTDOT, ".."); return
        if two == "::":
            self.advance(); self.advance(); self.add(TT.DCOLON, "::"); return
        if two == "+=":
            self.advance(); self.advance(); self.add(TT.PLUS_EQ, "+="); return
        if two == "-=":
            self.advance(); self.advance(); self.add(TT.MINUS_EQ, "-="); return
        if two == "*=":
            self.advance(); self.advance(); self.add(TT.STAR_EQ, "*="); return
        if two == "/=":
            self.advance(); self.advance(); self.add(TT.SLASH_EQ, "/="); return
        # Compact mode two-char (symbol mode inherits these)
        if self.mode in ("compact", "symbol"):
            if two == "<-":
                self.advance(); self.advance(); self.add(TT.RETURN, "<-"); return
            if two == "<<":
                self.advance(); self.advance(); self.add(TT.IMPORT, "<<"); return
            if two == ">>":
                self.advance(); self.advance(); self.add(TT.PRINT, ">>"); return
            if two == "~~":
                self.advance(); self.advance(); self.add(TT.WHILE, "~~"); return
            if two == "??":
                self.advance(); self.advance(); self.add(TT.ELIF, "??"); return

        # Single-character operators
        singles = {
            "+": TT.PLUS, "-": TT.MINUS, "*": TT.STAR, "/": TT.SLASH,
            "%": TT.PERCENT, "=": TT.ASSIGN, "<": TT.LT, ">": TT.GT,
            ".": TT.DOT, ":": TT.COLON, ",": TT.COMMA,
            "(": TT.LPAREN, ")": TT.RPAREN,
            "[": TT.LBRACKET, "]": TT.RBRACKET,
            "{": TT.LBRACE, "}": TT.RBRACE,
            "@": TT.AT, "?": TT.QUESTION, "&": TT.AMP, "~": TT.TILDE,
            "_": TT.UNDERSCORE,
        }

        # Compact mode single-char overrides (symbol mode inherits these)
        if self.mode in ("compact", "symbol"):
            if ch == "@":
                self.advance(); self.add(TT.LET, "@"); return
            if ch == "?":
                self.advance(); self.add(TT.IF, "?"); return
            if ch == "!":
                self.advance(); self.add(TT.ELSE, "!"); return
            if ch == "~":
                self.advance(); self.add(TT.FOR, "~"); return

        if ch in singles:
            self.advance()
            self.add(singles[ch], ch)
            return

        self.error(f"Unexpected character: {ch!r}")

    def _read_string(self, quote: str):
        """Read a string literal, handling interpolation {expr}"""
        self.advance()  # skip opening quote
        value = ""
        has_interp = False
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if ch == "\\":
                self.advance()
                if self.pos < len(self.source):
                    esc = self.advance()
                    escape_map = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"', "{": "{", "}": "}"}
                    value += escape_map.get(esc, "\\" + esc)
                continue
            if ch == quote:
                self.advance()
                break
            if ch == "{":
                has_interp = True
            value += self.advance()

        self.add(TT.STRING, value)

    def _read_number(self):
        """Read integer or float literal"""
        start = self.pos
        is_float = False

        # Hex
        if self.source[self.pos] == "0" and self.pos + 1 < len(self.source) and self.source[self.pos + 1] in "xX":
            self.advance()
            self.advance()
            while self.pos < len(self.source) and self.source[self.pos] in "0123456789abcdefABCDEF_":
                self.advance()
            self.add(TT.INTEGER, self.source[start:self.pos].replace("_", ""))
            return

        while self.pos < len(self.source) and (self.source[self.pos].isdigit() or self.source[self.pos] == "_"):
            self.advance()

        if self.pos < len(self.source) and self.source[self.pos] == "." and self.peek(1).isdigit():
            is_float = True
            self.advance()
            while self.pos < len(self.source) and (self.source[self.pos].isdigit() or self.source[self.pos] == "_"):
                self.advance()

        # Scientific notation
        if self.pos < len(self.source) and self.source[self.pos] in "eE":
            is_float = True
            self.advance()
            if self.pos < len(self.source) and self.source[self.pos] in "+-":
                self.advance()
            while self.pos < len(self.source) and self.source[self.pos].isdigit():
                self.advance()

        value = self.source[start:self.pos].replace("_", "")
        self.add(TT.FLOAT if is_float else TT.INTEGER, value)

    def _read_identifier(self):
        """Read identifier or keyword"""
        start = self.pos
        while self.pos < len(self.source) and (self.source[self.pos].isalnum() or self.source[self.pos] == "_"):
            self.advance()
        word = self.source[start:self.pos]

        # Check keywords based on mode
        kw_map = NORMAL_KEYWORDS if self.mode == "normal" else COMPACT_KEYWORDS
        if word in kw_map:
            self.add(kw_map[word], word)
        else:
            self.add(TT.IDENT, word)


def lex(source: str, filename: str = "<stdin>") -> List[Token]:
    """Convenience function to tokenize source code"""
    return Lexer(source, filename).tokenize()

import re


def _lost_lines(original: str, replacement: str) -> int:
    """Blocks collapse to one line; pad so every later line keeps its number
    (diagnostics, the LSP and the debugger all point at the real source)."""
    return max(0, original.count("\n") - replacement.count("\n"))


_BLOCKS = {"frontend": "__frontend", "html": "__html", "css": "__css"}

def _extract(src, keyword, varname):
    out = []
    i = 0
    while True:
        m = re.search(r'(^|\n)[ \t]*' + keyword + r'\s*\{', src[i:])
        if not m:
            out.append(src[i:])
            break
        start = i + m.start()
        brace = i + m.end() - 1
        out.append(src[i:start])
        depth = 0
        j = brace
        while j < len(src):
            if src[j] == '{': depth += 1
            elif src[j] == '}':
                depth -= 1
                if depth == 0: break
            j += 1
        raw = src[brace+1:j]
        prefix = m.group(1)
        import base64 as _b64
        b = _b64.b64encode(raw.encode()).decode()
        rep = f'{prefix}{varname} = __rawb64("{b}")'
        out.append(rep + "\n" * _lost_lines(src[start:j + 1], rep))
        i = j + 1
    return "".join(out)

def _extract_ui(src):
    """ui{ markup } -> __ui = compiled HTML (base64). Optionally reads a css{} block."""
    from compiler.uicompile import build_page
    out = []
    i = 0
    # grab a css{} block if present to feed into the page
    css_match = re.search(r'(^|\n)[ \t]*css\s*\{', src)
    css_block = ""
    if css_match:
        b = css_match.end() - 1; depth=0; j=b
        while j < len(src):
            if src[j]=='{':depth+=1
            elif src[j]=='}':
                depth-=1
                if depth==0:break
            j+=1
        css_block = src[b+1:j]
    while True:
        m = re.search(r'(^|\n)[ \t]*ui\s*\{', src[i:])
        if not m:
            out.append(src[i:]); break
        start = i+m.start(); brace=i+m.end()-1
        out.append(src[i:start])
        depth=0; j=brace
        while j < len(src):
            if src[j]=='{':depth+=1
            elif src[j]=='}':
                depth-=1
                if depth==0:break
            j+=1
        raw = src[brace+1:j]
        page = build_page(raw, css_block)
        import base64 as _b64
        b64 = _b64.b64encode(page.encode()).decode()
        rep = m.group(1) + f'__ui = __rawb64("{b64}")'
        out.append(rep + "\n" * _lost_lines(src[start:j + 1], rep))
        i = j+1
    return "".join(out)



_BRIDGE_LANGS = ("java", "js", "python", "php", "ruby", "perl", "go", "rust", "r")

def _extract_bridge_blocks(src: str) -> str:
    """java> { ... }  /  js> { ... }  →  __bridge_exec("java", b64)
    Each block runs in its real runtime, hosted by Sym. Shared data via `sym`."""
    import base64 as _b64
    for lang in _BRIDGE_LANGS:
        out = []
        i = 0
        while True:
            m = re.search(r'(^|\n)([ \t]*)' + lang + r'>\s*\{', src[i:])
            if not m:
                out.append(src[i:])
                break
            start = i + m.start()
            brace = i + m.end() - 1
            out.append(src[i:start])
            depth = 0
            j = brace
            quote = None          # inside '...', "...", or `...`?
            while j < len(src):
                ch = src[j]
                if quote:
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == quote:
                        quote = None
                elif ch in ("'", '"', "`"):
                    quote = ch
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            raw = src[brace + 1:j]
            b = _b64.b64encode(raw.encode()).decode()
            rep = f'{m.group(1)}{m.group(2)}__bridge_exec("{lang}", "{b}")'
            out.append(rep + "\n" * _lost_lines(src[start:j + 1], rep))
            i = j + 1
        src = "".join(out)
    return src


def _rewrite_bridge_imports(src: str) -> str:
    """java.import math.Calculator [as calc] → let calc = __java_import("math.Calculator")
    Python never sees Java. Both talk to Sym.
    The `let` is spelled for the file's mode — in #compact/#symbol files the word
    `let` is an ordinary identifier, so emitting it silently broke bridge imports."""
    first = src.split("\n", 1)[0].strip()
    let_kw = {"#compact": "@", "#symbol": "∂"}.get(first, "let")
    def repl(m):
        lang, target, alias = m.group(1), m.group(2), m.group(3)
        name = alias or target.split(".")[-1]
        return f'{m.group(0).split(lang)[0]}let {name} = __{lang}_import("{target}")'
    return re.sub(
        r'(?:^|(?<=\n))([ \t]*)(?:)(java|js|php|ruby|perl|c|r|sym)\.import[ \t]+([\w.:]+)(?:[ \t]+as[ \t]+(\w+))?',
        lambda m: f'{m.group(1)}{let_kw} {m.group(4) or m.group(3).split(".")[-1]} = '
                  f'__{m.group(2)}_import("{m.group(3)}")',
        src)


def preprocess(src: str) -> str:
    src = _extract_ui(src)
    src = _extract_bridge_blocks(src)
    src = _rewrite_bridge_imports(src)
    for kw, var in _BLOCKS.items():
        src = _extract(src, kw, var)
    return src


# ── Embedded regions (tooling) ─────────────────────────────────
EMBED_LANGS = _BRIDGE_LANGS + ("c", "sym")
_RAW_BLOCKS = ("ui", "css", "html", "frontend")


def _match_brace(src: str, brace: int, string_aware: bool) -> int:
    depth, j, quote = 0, brace, None
    while j < len(src):
        ch = src[j]
        if quote:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                quote = None
        elif string_aware and ch in ("'", '"', "`"):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return len(src)


def embedded_regions(src: str):
    """Every foreign-language region in a .sym file, without rewriting it.

    Returns dicts: {lang, start, body_start, body_end, end} as code-point
    offsets. `lang` is a bridge language (js, java, ...) or ui/css/html/frontend.
    The editor uses these for language injection; morph uses them to leave
    foreign code untouched."""
    regions = []
    pat = re.compile(r'(^|\n)([ \t]*)(' + "|".join(_BRIDGE_LANGS) + r')>\s*\{'
                     r'|(^|\n)([ \t]*)(' + "|".join(_RAW_BLOCKS) + r')\s*\{')
    i = 0
    while True:
        m = pat.search(src, i)
        if not m:
            break
        lang = m.group(3) or m.group(6)
        lead = m.group(1) if m.group(3) else m.group(4)
        start = m.start() + len(lead)
        brace = m.end() - 1
        close = _match_brace(src, brace, string_aware=lang in _BRIDGE_LANGS)
        regions.append({"lang": lang, "start": start, "body_start": brace + 1,
                        "body_end": close, "end": min(close + 1, len(src))})
        i = close + 1 if close < len(src) else len(src)
        if i >= len(src):
            break
    return regions


def mask_embedded(src: str, regions=None) -> str:
    """Blank out embedded bodies (keeping newlines) so the Sym lexer can tokenize
    the surrounding Sym with exact offsets."""
    regions = embedded_regions(src) if regions is None else regions
    out = list(src)
    for r in regions:
        for k in range(r["body_start"], r["body_end"]):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)

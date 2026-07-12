import re

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
        out.append(f'{prefix}{varname} = __rawb64("{b}")')
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
        out.append(m.group(1) + f'__ui = __rawb64("{b64}")')
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
            out.append(f'{m.group(1)}{m.group(2)}__bridge_exec("{lang}", "{b}")')
            i = j + 1
        src = "".join(out)
    return src


def _rewrite_bridge_imports(src: str) -> str:
    """java.import math.Calculator [as calc] → let calc = __java_import("math.Calculator")
    Python never sees Java. Both talk to Sym."""
    def repl(m):
        lang, target, alias = m.group(1), m.group(2), m.group(3)
        name = alias or target.split(".")[-1]
        return f'{m.group(0).split(lang)[0]}let {name} = __{lang}_import("{target}")'
    return re.sub(
        r'(?:^|(?<=\n))([ \t]*)(?:)(java|js|php|ruby|perl|c|r|sym)\.import[ \t]+([\w.:]+)(?:[ \t]+as[ \t]+(\w+))?',
        lambda m: f'{m.group(1)}let {m.group(4) or m.group(3).split(".")[-1]} = '
                  f'__{m.group(2)}_import("{m.group(3)}")',
        src)


def preprocess(src: str) -> str:
    src = _extract_ui(src)
    src = _extract_bridge_blocks(src)
    src = _rewrite_bridge_imports(src)
    for kw, var in _BLOCKS.items():
        src = _extract(src, kw, var)
    return src

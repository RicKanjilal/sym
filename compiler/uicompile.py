# uicompile.py - Sym UI language -> HTML.
# Indentation-based markup. Cleaner than HTML: no closing tags, no < >.
#
#   ui{
#     page "My App"
#     col .container
#       h1 "Hello"
#       text "some paragraph"
#       row .toolbar
#         button #save "Save"
#         input @username "type name"
#       img "logo.png"
#       link "/home" "Go home"
#       raw <svg>...</svg>
#   }
#
# Rules:
#   tagword [.class] [#id] [@name] ["text"]   → element
#   indentation = nesting
#   .x → class, #x → id, @x → name attribute
#   "..." trailing = inner text (or attr for input/img/link)
#   css{ } blocks pass straight through into <style>

import re

# map friendly tag words -> real HTML tags
TAGS = {
    "page": "__PAGE__", "col": "div", "row": "div", "box": "div",
    "text": "p", "h1": "h1", "h2": "h2", "h3": "h3", "h4": "h4",
    "button": "button", "btn": "button", "input": "input", "img": "img",
    "link": "a", "list": "ul", "item": "li", "span": "span",
    "header": "header", "footer": "footer", "nav": "nav", "section": "section",
    "label": "label", "title": "h1", "sub": "h2", "card": "div",
    "form": "form", "area": "textarea", "sel": "select", "opt": "option",
}
# tags that use text as an attribute, not innerText
SELF_CLOSING = {"input", "img"}
FLEX_ROW = {"row"}
FLEX_COL = {"col"}

def _parse_token_line(line):
    """Return (tag, classes, id, name, text, extra_attrs)."""
    s = line.strip()
    # raw passthrough
    if s.startswith("raw "):
        return ("__RAW__", [], None, None, s[4:], {})
    if s.startswith("css "):
        return ("__CSS__", [], None, None, s[4:], {})
    # extract trailing quoted strings (can be 1 or 2: link "/x" "label")
    strings = re.findall(r'"([^"]*)"', s)
    s_nostr = re.sub(r'"[^"]*"', '', s)
    parts = s_nostr.split()
    if not parts:
        return None
    tag = parts[0]
    classes, tid, name = [], None, None
    events = {}
    for p in parts[1:]:
        if p.startswith("."): classes.append(p[1:])
        elif p.startswith("#"): tid = p[1:]
        elif "=" in p and p.split("=")[0].lstrip("@") in ("click","input","change","submit","keydown"):
            k,v = p.split("=",1); events[k.lstrip("@")] = v
        elif p.startswith("@"): name = p[1:]
    return (tag, classes, tid, name, strings, events)

def compile_ui(src_block: str) -> str:
    """Compile a UI block body (indented markup) into HTML."""
    lines = [l for l in src_block.split("\n") if l.strip()]
    html = []
    stack = []  # (indent, close_tag)
    page_title = "Sym App"
    styles = []

    def indent_of(l): return len(l) - len(l.lstrip())

    body = []
    for line in lines:
        ind = indent_of(line)
        parsed = _parse_token_line(line)
        if not parsed:
            continue
        tag, classes, tid, name, strings, _ = parsed

        # close deeper/equal elements
        while stack and stack[-1][0] >= ind:
            body.append("  " * stack[-1][0] + stack[-1][1])
            stack.pop()

        if tag == "page":
            if strings: page_title = strings[0]
            continue
        if tag == "__RAW__":
            body.append(strings if isinstance(strings, str) else "")
            continue
        if tag == "__CSS__":
            continue

        real = TAGS.get(tag, tag)  # unknown word -> use as-is (custom tag)
        attrs = ""
        cls = list(classes)
        if tag in FLEX_ROW: cls.append("__row")
        if tag in FLEX_COL: cls.append("__col")
        if cls: attrs += f' class="{" ".join(cls)}"'
        if tid: attrs += f' id="{tid}"'
        if name: attrs += f' name="{name}"'
        for _ev, _fn in _.items():
            attrs += f' on{_ev}="{_fn}(event)"'

        text = strings[0] if strings else ""

        if tag == "link":
            href = strings[0] if strings else "#"
            label = strings[1] if len(strings) > 1 else href
            body.append("  " * ind + f'<a href="{href}"{attrs}>{label}</a>')
            continue
        if tag == "input":
            ph = text
            body.append("  " * ind + f'<input{attrs} placeholder="{ph}">')
            continue
        if tag == "img":
            body.append("  " * ind + f'<img src="{text}"{attrs}>')
            continue

        # open element; text as inner content
        body.append("  " * ind + f'<{real}{attrs}>{text}')
        stack.append((ind, f'</{real}>'))

    # close remaining
    while stack:
        body.append("  " * stack[-1][0] + stack[-1][1])
        stack.pop()

    base_css = """
.__row{display:flex;flex-direction:row;gap:8px}
.__col{display:flex;flex-direction:column;gap:8px}
"""
    return page_title, base_css, "\n".join(body)


def build_page(ui_block: str, css_block: str = "") -> str:
    title, base_css, body = compile_ui(ui_block)
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>{base_css}\n{css_block}</style></head>"
            f"<body>{body}</body></html>")

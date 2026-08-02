#!/usr/bin/env python3
"""HTML -> Markdown conversion, shared by the OCR and office-document converters.

Handles the tag subset Chandra OCR emits and the subset LibreOffice writes on
HTML export: headings, paragraphs, tables (with colspan flattening), lists,
inline emphasis, math, code and links. Pure bs4 — no PDF/OCR dependencies, so
it imports cheaply inside a LibreOffice container.
"""
from bs4 import BeautifulSoup, NavigableString, Tag

# Sentinel for <br>. A literal "  \n" would be indistinguishable from ordinary
# source-wrapping whitespace, which LibreOffice's HTML export emits everywhere.
BR = "\x00br\x00"


def _cell_text(el):
    return " ".join(el.get_text(" ", strip=True).split())


def _table_md(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = [_cell_text(c) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _emph(inner, marker):
    """Wrap in an emphasis marker, keeping any whitespace outside it.

    Word processors split a styled phrase into several adjacent runs; folding
    their spaces inside the markers would glue words together, and emitting
    `**a****b**` for two touching runs renders as literal asterisks.
    """
    core = inner.strip()
    if not core:
        return inner
    lead = inner[:len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f"{lead}{marker}{core}{marker}{trail}"


def _inline(node):
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    name = node.name
    inner = "".join(_inline(c) for c in node.children)
    if name in ("b", "strong", "big"):
        return _emph(inner, "**")
    if name in ("i", "em"):
        return _emph(inner, "*")
    if name == "del":
        return _emph(inner, "~~")
    if name == "sup":
        return f"^{inner}^"
    if name == "sub":
        return f"_{inner}_"
    if name == "br":
        return BR
    if name == "math":
        expr = inner.strip()
        return f"$$ {expr} $$" if node.get("display") else f"${expr}$"
    if name == "chem":
        return f"`{inner.strip()}`"
    if name == "code":
        return f"`{inner.strip()}`"
    if name == "a":
        href = node.get("href")
        return f"[{inner.strip()}]({href})" if href else inner
    if name == "img":
        alt = (node.get("alt") or "").strip()
        return f"![{alt}]()" if alt else ""
    return inner  # span, u, small, input, etc. -> pass through


def _para(txt):
    """Collapse HTML source whitespace, keep explicit <br> hard breaks.

    Newlines inside an HTML text node are just whitespace (LibreOffice wraps its
    exported source), so a paragraph must be re-joined; only the BR sentinels
    `_inline` emits for <br> are real line breaks.
    """
    txt = "  \n".join(" ".join(part.split()) for part in txt.split(BR)).strip()
    # Two touching runs of the same style: "**a****b**" is one phrase, not an
    # empty emphasis. Merge before a renderer reads the markers literally.
    for marker in ("****", "~~~~"):
        txt = txt.replace(marker, "")
    return txt


def _block(node, out):
    if isinstance(node, NavigableString):
        t = _para(str(node))
        if t:
            out.append(t)
        return
    if not isinstance(node, Tag):
        return
    name = node.name
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        lvl = int(name[1])
        txt = " ".join(_inline(node).replace(BR, " ").split())
        if txt:
            out.append("#" * lvl + " " + txt)
        return
    if name == "p":
        txt = _para(_inline(node))
        if txt:
            out.append(txt)
        return
    if name == "table":
        md = _table_md(node)
        if md:
            out.append(md)
        return
    if name in ("ul", "ol"):
        ordered = name == "ol"
        for i, li in enumerate(node.find_all("li", recursive=False), 1):
            marker = f"{i}." if ordered else "-"
            txt = " ".join(_inline(li).replace(BR, " ").split())
            if txt:
                out.append(f"{marker} {txt}")
        return
    if name == "hr":
        out.append("---")
        return
    if name == "pre":
        out.append("```\n" + node.get_text() + "\n```")
        return
    if name in ("div", "span", "tbody", "thead", "caption", "small"):
        # container: recurse into children as blocks
        children = [c for c in node.children if isinstance(c, (Tag, NavigableString))]
        if any(isinstance(c, Tag) and c.name in
               ("p", "div", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "pre")
               for c in children):
            for c in children:
                _block(c, out)
        else:
            txt = _para(_inline(node))
            if txt:
                out.append(txt)
        return
    # fallback: treat as inline block
    txt = _para(_inline(node))
    if txt:
        out.append(txt)


def html_to_md(html):
    soup = BeautifulSoup(html or "", "html.parser")
    root = soup.body or soup
    out = []
    for child in root.children:
        _block(child, out)
    # collapse excessive blank lines
    return "\n\n".join(b for b in (x.strip() for x in out) if b)


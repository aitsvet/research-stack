#!/usr/bin/env python3
"""Page-numbered table of contents for md_pdf.sh — a second pass over the built PDF.

    md_toc.py doc.md doc.pdf [--levels 2,3]

The markdown carries an empty block `<!-- toc -->` … `<!-- /toc -->` right under
its TOC heading. This finds the page of every heading of the given levels in the
already printed PDF and rewrites the block as a two-column table (title, page);
then build the PDF again with md_pdf.sh. Headings above the block are searched
from the first page, headings below it only after the TOC page, so the TOC's own
entries are never matched. Assumes the TOC fits one page and ends with a page
break — otherwise numbers shift; re-run and compare if in doubt.
"""
import argparse
import html
import pathlib
import re
import sys

import pymupdf

START, END = "<!-- toc -->", "<!-- /toc -->"


def plain(s):
    s = re.sub(r"<[^>]+>", "", s)
    return " ".join(re.sub(r"[*_`]", "", s).split())


def norm(s):
    return re.sub(r"\s+", " ", s.replace("­", "")).strip().lower()


def headings(text, levels):
    out, fence = [], False
    for ln in text.split("\n"):
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", ln)
        if m and not fence and len(m.group(1)) in levels:
            out.append((len(m.group(1)), plain(m.group(2))))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md")
    ap.add_argument("pdf")
    ap.add_argument("--levels", default="2,3", help="heading levels to list (default 2,3)")
    a = ap.parse_args()
    levels = {int(x) for x in a.levels.split(",")}
    path = pathlib.Path(a.md)
    src = path.read_text(encoding="utf-8")
    if START not in src or END not in src:
        sys.exit(f"no {START} … {END} block in {a.md}")
    head, rest = src.split(START, 1)
    _, tail = rest.split(END, 1)
    before, after = headings(head, levels | {2}), headings(tail, levels)
    if not before:
        sys.exit("the TOC block needs a heading right above it")
    toc_title = before.pop()[1]
    before = [h for h in before if h[0] in levels]
    pages = [norm(p.get_text()) for p in pymupdf.open(a.pdf)]

    def locate(title, start):
        key = norm(title)[:60]
        return next((i for i in range(start, len(pages)) if key in pages[i]), None)

    rows, missing, cur = [], [], 0
    for lvl, title in before:
        i = locate(title, cur)
        if i is None:
            missing.append(title)
        else:
            cur = i
        rows.append((lvl, title, i))
    toc_page = locate(toc_title, cur)
    cur = (toc_page if toc_page is not None else cur) + 1
    for lvl, title in after:
        i = locate(title, cur)
        if i is None:
            missing.append(title)
        else:
            cur = i
        rows.append((lvl, title, i))

    base = min(levels)
    cells = "\n".join(
        f'<tr><td style="padding:1px 0 1px {(lvl - base) * 1.5}em">{html.escape(t)}</td>'
        f'<td style="text-align:right;vertical-align:bottom;padding-left:1em">'
        f'{"" if i is None else i + 1}</td></tr>'
        for lvl, t, i in rows)
    table = f'<table style="width:100%;border-collapse:collapse">\n{cells}\n</table>'
    path.write_text(f"{head}{START}\n{table}\n{END}{tail}", encoding="utf-8")
    print(f"{len(rows)} entries, TOC on page {None if toc_page is None else toc_page + 1}"
          + (f"; NOT FOUND: {missing}" if missing else ""))


if __name__ == "__main__":
    main()

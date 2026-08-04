#!/usr/bin/env python3
"""Pull tables out of a corpus into JSON, so matrices stay data instead of prose.

Long specifications carry their real content in tables — «сообщение → действие»,
«параметр → значение», «шаг → контроль». Two things go wrong with them:

* Converted markdown keeps a pipe table only when the source was a Word file.
  A PDF table becomes flowing text, and the values drift away from their rows —
  a column of numbers ends up as a paragraph after three merged row labels.
  Reading such a table off the markdown produces confident, wrong pairings.
* Even when the table survives, retyping dozens of near-identical rows into
  prose adds nothing over the rule they follow, and the rows are exactly what a
  traceability matrix needs later.

So: extract tables mechanically, keep them as data next to the register, and
cite rows by index instead of transcribing them.

    extract_tables.py md  <file|dir> [--out F] [--min-rows N] [--grep RE]
    extract_tables.py pdf <file.pdf> [--out F] [--pages A-B] [--min-rows N]

`md` reads pipe tables and records, for each, the nearest preceding heading and
the nearest preceding `{N}` page mark, so every row keeps an anchor back to the
page of the original document. `pdf` uses PyMuPDF's table finder, which works
off ruling lines and text geometry — use it whenever the markdown lost the
structure, and compare the two when in doubt.
"""
import argparse, glob, json, os, re, sys

PAGE_MARK = re.compile(r"^\{(\d+)\}")
HEADING = re.compile(r"^#{1,6}\s+(.*\S)")
BOLD_HEADING = re.compile(r"^\*\*(.+?)\*\*\s*$")
ROW = re.compile(r"^\s*\|(.+)\|\s*$")
SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def cells(line):
    return [c.strip() for c in ROW.match(line).group(1).split("|")]


def clean(text):
    return re.sub(r"\s+", " ", re.sub(r"\*\*|__", "", text)).strip()


def from_markdown(path, min_rows):
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    page, heading, out, i = None, None, [], 0
    while i < len(lines):
        line = lines[i]
        m = PAGE_MARK.match(line)
        if m:
            page = int(m.group(1))
        h = HEADING.match(line) or BOLD_HEADING.match(line)
        if h:
            heading = clean(h.group(1))[:200]
        if ROW.match(line):
            block = []
            while i < len(lines) and ROW.match(lines[i]):
                if not SEPARATOR.match(lines[i]):
                    block.append(cells(lines[i]))
                i += 1
            if len(block) >= min_rows:
                header = [clean(c) for c in block[0]]
                rows = [[clean(c) for c in r] for r in block[1:]]
                out.append(dict(source=path, page=page, heading=heading,
                                header=header, rows=rows, n_rows=len(rows)))
            continue
        i += 1
    return out


def from_pdf(path, pages, min_rows):
    import fitz
    doc = fitz.open(path)
    lo, hi = (pages if pages else (1, doc.page_count))
    out = []
    for pno in range(lo - 1, min(hi, doc.page_count)):
        page = doc[pno]
        try:
            found = page.find_tables()
        except Exception as exc:                      # older PyMuPDF
            print(f"  find_tables unavailable: {exc}", file=sys.stderr)
            return out
        for t in found.tables:
            grid = [[clean(c or "") for c in row] for row in t.extract()]
            grid = [r for r in grid if any(c for c in r)]
            if len(grid) < min_rows:
                continue
            out.append(dict(source=path, page=pno + 1, heading=None,
                            header=grid[0], rows=grid[1:], n_rows=len(grid) - 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["md", "pdf"])
    ap.add_argument("path")
    ap.add_argument("--out", help="write JSON here (default: stdout summary only)")
    ap.add_argument("--min-rows", type=int, default=3,
                    help="skip tables smaller than this many rows (default 3)")
    ap.add_argument("--pages", help="pdf mode: page range like 90-99 (1-based)")
    ap.add_argument("--grep", help="keep only tables whose heading or any cell matches this regex")
    a = ap.parse_args()

    if a.mode == "md":
        targets = ([a.path] if os.path.isfile(a.path)
                   else sorted(glob.glob(os.path.join(a.path, "**", "*.md"), recursive=True)))
        tables = [t for p in targets for t in from_markdown(p, a.min_rows)]
    else:
        rng = None
        if a.pages:
            lo, _, hi = a.pages.partition("-")
            rng = (int(lo), int(hi or lo))
        tables = from_pdf(a.path, rng, a.min_rows)

    if a.grep:
        rx = re.compile(a.grep, re.I)
        tables = [t for t in tables
                  if rx.search(t["heading"] or "")
                  or any(rx.search(c) for c in t["header"])
                  or any(rx.search(c) for r in t["rows"] for c in r)]

    for n, t in enumerate(tables):
        t["id"] = n
    if a.out:
        json.dump(tables, open(a.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    print(f"{len(tables)} таблиц, {sum(t['n_rows'] for t in tables)} строк"
          + (f" -> {a.out}" if a.out else ""))
    for t in tables[:25]:
        head = " | ".join(t["header"])[:90]
        print(f"  [{t['id']:3d}] стр {str(t['page']):>4}  {t['n_rows']:4d} строк  {head}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Extract text/markdown from PDFs, two ways:

1. Zotero-collection mode (default): walk a Zotero collection. For every parent
   item with a child PDF attachment, extract the full text layer in reading order
   (`pdftotext` without `-layout`, pymupdf as a fallback), one `{N}`+48-dash page
   mark per page, line breaks kept. Items without a PDF fall back to Zotero's
   `.zotero-ft-cache`, then to a stored HTML snapshot. Save to
   <DISCOVERY_OUT>/text/<itemKey>.txt plus a JSON manifest carrying, per item,
   the extractor, page count, character count and a completeness verdict.

2. Markdown mode (`md` subcommand): pure-python, no GPU/OCR. Convert one or more
   PDFs (or every *.pdf in a directory) to Markdown via pymupdf4llm's text-layer
   reader, emitting the same `{N}`+48-dash paginated format the marker pipeline
   produces. Because it reads the embedded text layer (not OCR), it avoids
   marker's homoglyph errors (Latin acronyms mis-rendered as Cyrillic, dropped words),
   so its output is a clean reference for diffing/patching marker `.md` files.

Text is never cut silently. `--max-chars N` is an explicit excerpt mode: the file
is cut, and the manifest records `"truncated": true` with the full length.

Usage:
  extract_texts.py <collectionKey> [--max-chars N]   # Zotero-collection mode
  extract_texts.py refresh <text_manifest.json> [--max-chars N]
                                                     # re-extract the items an existing
                                                     # manifest lists, by attachment key
  extract_texts.py check <text-dir> [--manifest F]   # completeness audit, exit 1 on failure
  extract_texts.py md <pdf-or-dir> [...] [--out DIR] [--suffix .txt] [--stdout]

Items a manifest records under any other `src` (placed by an out-of-band extractor,
e.g. a .docx converter) are never overwritten by `<collectionKey>` or `refresh`.

Env:
  ZOTERO_MCP_TOKEN  required for collection mode.
  ZOTERO_STORAGE    path to Zotero `storage/` dir (default ~/research-stack/config/Zotero/storage).
  DISCOVERY_OUT     output dir parent (default ~/research-stack/.discovery).
"""
import json, os, re, sys, subprocess, glob
import acqlog

SEP_DASHES = 48  # marker-compatible page rule width
ZSTORE  = os.environ.get("ZOTERO_STORAGE",
                         os.path.expanduser("~/research-stack/config/Zotero/storage"))
DISC    = os.environ.get("DISCOVERY_OUT", os.path.expanduser("~/research-stack/.discovery"))
OUTDIR  = os.path.join(DISC, "text")


def _portable(p):
    """Manifest paths must not carry the host's home: storage-relative for
    Zotero files, `~/`-relative otherwise."""
    if not p:
        return p
    ap = os.path.abspath(p)
    if ap.startswith(os.path.abspath(ZSTORE) + os.sep):
        return "storage/" + os.path.relpath(ap, ZSTORE)
    home = os.path.expanduser("~")
    return "~" + ap[len(home):] if ap.startswith(home + os.sep) else p

# Completeness floor: a text layer averaging fewer characters per page than this is
# a scan, a cover-only layer or a failed extraction, not a readable paper.
MIN_CHARS_PER_PAGE = 400
# `src` values this script writes; anything else in a manifest came from elsewhere.
OWN_SRCS = {"pdftotext", "ft-cache", "html", "none", "external"}
PAGE_MARK = re.compile(r"^\{(\d+)\}-{%d}$" % SEP_DASHES, re.M)


def find_pdf(att_key):
    """Look for a PDF in the attachment's storage dir."""
    d = os.path.join(ZSTORE, att_key)
    if not os.path.isdir(d): return None
    pdfs = glob.glob(os.path.join(d, "*.pdf"))
    return pdfs[0] if pdfs else None

def find_ft_cache(att_key):
    d = os.path.join(ZSTORE, att_key)
    p = os.path.join(d, ".zotero-ft-cache")
    return p if os.path.isfile(p) and os.path.getsize(p) > 0 else None

def find_html(att_key):
    d = os.path.join(ZSTORE, att_key)
    if not os.path.isdir(d): return None
    hs = glob.glob(os.path.join(d, "*.html")) + glob.glob(os.path.join(d, "*.htm"))
    return hs[0] if hs else None

def _write_if_nonempty(out_path, text):
    """Write text to out_path, but never clobber an existing non-empty file with empty content."""
    text = (text or "")
    if not text.strip():
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            print(f"  WARN: preserving existing {os.path.basename(out_path)} ({os.path.getsize(out_path)} bytes); new content empty", file=sys.stderr)
            return -1
        return 0
    with open(out_path, "w") as f:
        f.write(text)
    return len(text)

def _pdftotext_pages(pdf):
    """Per-page text via poppler's pdftotext in reading order (no `-layout`: that
    keeps columns side by side and interleaves them). Raises if the binary is absent."""
    out = subprocess.check_output(["pdftotext", "-q", "-enc", "UTF-8", pdf, "-"],
                                  stderr=subprocess.DEVNULL, timeout=600).decode("utf-8", "replace")
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()                    # pdftotext ends every page, the last one too, with \f
    return pages

def _pymupdf_pages(pdf):
    """Pure-python fallback when pdftotext is unavailable (uses the same pymupdf
    dependency the `md` mode already relies on). Reads the embedded text layer."""
    import pymupdf
    doc = pymupdf.open(pdf)
    try:
        return [page.get_text("text") for page in doc]
    finally:
        doc.close()

def _clean(text):
    """Tidy whitespace without destroying structure: runs of blanks inside a line
    shrink to one space, trailing blanks go, 3+ newlines become one blank line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def paginate(pages, sep_dashes=SEP_DASHES):
    rule = "-" * sep_dashes
    return "".join(f"{{{i}}}{rule}\n\n{p}\n\n" for i, p in enumerate(pages))

def assess(pages):
    """Completeness facts for a paged extraction. `complete` is False when any flag
    is raised; the flags say why, so a reader knows what to look at."""
    n = len(pages)
    chars = sum(len(p) for p in pages)
    empty = sum(1 for p in pages if not p.strip())
    flags = []
    if n == 0:
        flags.append("no pages")
    else:
        if chars / n < MIN_CHARS_PER_PAGE:
            flags.append(f"{chars // n} chars/page < {MIN_CHARS_PER_PAGE}")
        if not pages[-1].strip():
            flags.append("last page empty")
        if empty * 2 > n:
            flags.append(f"{empty}/{n} pages empty")
    return {"pages": n, "chars": chars, "empty_pages": empty,
            "complete": not flags, "flags": flags}

def extract_pdf_text(pdf, out_path, max_chars=None):
    """Extract a PDF's whole text layer, paged, into out_path. Prefers pdftotext;
    falls back to pymupdf. Returns a manifest fragment (size -1 = extraction
    failed, 0 = nothing written). Never clobbers an existing non-empty .txt with
    empty output. `max_chars` is the explicit excerpt mode and is recorded."""
    try:
        pages, extractor = _pdftotext_pages(pdf), "pdftotext"
    except Exception:
        try:
            pages, extractor = _pymupdf_pages(pdf), "pymupdf"
        except Exception as e:
            return {"size": -1, "extractor": None, "error": str(e)[:200]}
    pages = [_clean(p) for p in pages]
    info = {"extractor": extractor, **assess(pages)}
    text = paginate(pages)
    info["full_chars"] = len(text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
        info["truncated"] = True
        info["complete"] = False
        info["flags"] = info["flags"] + [f"excerpt: first {max_chars} chars"]
    info["size"] = _write_if_nonempty(out_path, text)
    return info

def extract_flat_text(raw, out_path, extractor, max_chars=None):
    """ft-cache / HTML snapshot: no pages to count, so only length is recorded."""
    text = _clean(raw or "")
    info = {"extractor": extractor, "pages": None, "chars": len(text),
            "full_chars": len(text), "complete": bool(text), "flags": [] if text else ["empty"]}
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
        info.update(truncated=True, complete=False,
                    flags=[f"excerpt: first {max_chars} chars"])
    info["size"] = _write_if_nonempty(out_path, text)
    return info

def html_text(path):
    from bs4 import BeautifulSoup
    with open(path, encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        t.decompose()
    return soup.get_text("\n")

def pdf_to_markdown(pdf_path, sep_dashes=SEP_DASHES):
    """Pure-python PDF -> Markdown (pymupdf4llm text-layer reader, no OCR/GPU).

    Emits the marker-compatible paginated format: each page is prefixed by
    '{<page_index>}' immediately followed by a rule of `sep_dashes` dashes, then
    the page's markdown. Headings/bold come from font-size heuristics; images and
    vector graphics are ignored (text reference only)."""
    from pymupdf4llm.helpers import pymupdf_rag
    chunks = pymupdf_rag.to_markdown(
        str(pdf_path), page_chunks=True,
        ignore_images=True, ignore_graphics=True,
        show_progress=False,
    )
    rule = "-" * sep_dashes
    parts = []
    for i, ch in enumerate(chunks):
        text = (ch.get("text") if isinstance(ch, dict) else str(ch)) or ""
        parts.append(f"{{{i}}}{rule}\n\n{text.rstrip()}")
    return "\n\n" + "\n\n".join(parts) + "\n"


def _is_spread(page, ratio=1.15):
    """Two book pages photographed/typeset side by side on one PDF page.

    Detected by landscape page box plus text blocks living in both halves.
    """
    if page.rect.width < page.rect.height * ratio:
        return False
    mid = page.rect.width / 2
    left = right = False
    for b in page.get_text("blocks"):
        if not b[4].strip():
            continue
        if b[0] < mid * 0.9:
            left = True
        elif b[0] > mid * 1.05:
            right = True
    return left and right


def split_spreads(pdf_path, out_path):
    """Cut two-up spreads into single pages, preserving reading order.

    Why this matters: extracting a spread directly interleaves the left and
    right page top-to-bottom, so sentences break mid-clause and paragraphs from
    facing pages get stitched together. A quote pulled from such text looks
    genuine and is nonsense. Splitting first lets the normal pipeline read each
    book page in order.

    Lossless: pages are re-imaged by reference (`show_pdf_page`), nothing is
    re-rendered or re-encoded. Non-spread pages are copied through untouched.
    """
    import fitz
    src = fitz.open(str(pdf_path))
    out = fitz.open()
    n = 0
    for page in src:
        if not _is_spread(page):
            out.insert_pdf(src, from_page=page.number, to_page=page.number)
            continue
        n += 1
        mid = page.rect.width / 2
        halves = (fitz.Rect(page.rect.x0, page.rect.y0, mid, page.rect.y1),
                  fitz.Rect(mid, page.rect.y0, page.rect.x1, page.rect.y1))
        for rect in halves:
            new = out.new_page(width=rect.width, height=rect.height)
            new.show_pdf_page(new.rect, src, page.number, clip=rect)
    out.save(str(out_path))
    out.close(); src.close()
    return n


def cmd_md(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="extract_texts.py md",
        description="Convert PDFs to paginated Markdown via pymupdf4llm (no OCR).")
    ap.add_argument("paths", nargs="+", help="PDF files and/or directories of PDFs")
    ap.add_argument("--spreads", choices=["auto", "always", "never"], default="auto",
                    help="split two-up spreads into single pages first (default: auto)")
    ap.add_argument("--out", default=None,
                    help="output dir (default: alongside each source PDF)")
    ap.add_argument("--suffix", default=".txt",
                    help="output file suffix (default: .txt)")
    ap.add_argument("--stdout", action="store_true",
                    help="write to stdout instead of files (single PDF)")
    args = ap.parse_args(argv)

    targets = []
    for p in args.paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            targets += sorted(glob.glob(os.path.join(p, "*.pdf")))
        elif p.lower().endswith(".pdf") and os.path.isfile(p):
            targets.append(p)
        else:
            print(f"  skip (not a PDF/dir): {p}", file=sys.stderr)
    if not targets:
        sys.exit("no PDFs found")

    n_ok = 0
    for pdf in targets:
        tmp = None
        try:
            if args.spreads != "never":
                import tempfile
                tmp = os.path.join(tempfile.gettempdir(),
                                   os.path.basename(pdf) + ".split.pdf")
                n_split = split_spreads(pdf, tmp)
                if n_split or args.spreads == "always":
                    print(f"  {os.path.basename(pdf)}: spreads split: {n_split}",
                          file=sys.stderr)
                    pdf_src = tmp
                else:
                    pdf_src = pdf
            else:
                pdf_src = pdf
            md = pdf_to_markdown(pdf_src)
        except Exception as e:
            print(f"  FAIL {os.path.basename(pdf)}: {e}", file=sys.stderr)
            continue
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        if args.stdout:
            sys.stdout.write(md)
            n_ok += 1
            continue
        outdir = args.out or os.path.dirname(pdf)
        os.makedirs(outdir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(pdf))[0]
        out_path = os.path.join(outdir, stem + args.suffix)
        acqlog.record(path=pdf, extractor="text-layer", text=out_path,
                      pages=len(re.findall(r"^\{\d+\}-{48}$", md, re.M)) or None)
        with open(out_path, "w") as f:
            f.write(md)
        n_ok += 1
        print(f"  {os.path.basename(pdf)} -> {out_path} ({len(md)} chars)")
    print(f"\n{n_ok}/{len(targets)} PDFs converted to markdown")


def extract_item(key, att_keys, title, max_chars=None):
    """Extract one parent item from its attachments: PDF, else ft-cache, else HTML
    snapshot, else whatever text is already on disk. Returns its manifest entry."""
    out_path = os.path.join(OUTDIR, f"{key}.txt")
    for ak in att_keys:
        pdf = find_pdf(ak)
        if pdf:
            info = extract_pdf_text(pdf, out_path, max_chars)
            return {"key": key, "src": "pdftotext", "att": ak, "pdf": _portable(pdf), "title": title, **info}
    for ak in att_keys:
        ft = find_ft_cache(ak)
        if ft:
            with open(ft, encoding="utf-8", errors="replace") as f:
                info = extract_flat_text(f.read(), out_path, "zotero-ft-cache", max_chars)
            if info["size"] > 0:
                return {"key": key, "src": "ft-cache", "att": ak, "title": title, **info}
    for ak in att_keys:
        h = find_html(ak)
        if h:
            info = extract_flat_text(html_text(h), out_path, "html", max_chars)
            if info["size"] > 0:
                return {"key": key, "src": "html", "att": ak, "html": _portable(h), "title": title, **info}
    # Nothing extractable. Preserve any existing .txt (may have been placed manually
    # by an out-of-band extractor such as a .docx converter).
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return {"key": key, "src": "external", "att": None, "title": title,
                "size": len(open(out_path, encoding="utf-8", errors="replace").read()), "complete": None,
                "flags": ["kept existing text; not re-extracted"]}
    return {"key": key, "src": "none", "att": None, "title": title, "size": 0}

def _report(e):
    if e["src"] == "none":
        print(f"  {e['key']}: NO PDF FOUND  '{e['title']}'")
    else:
        pg = f"{e['pages']} pp, " if e.get("pages") else ""
        warn = ("  !! " + "; ".join(e["flags"])) if e.get("flags") else ""
        print(f"  {e['key']}: {e['src']}: {pg}{e.get('size')} chars{warn}")

def _run(entries, manifest_path, prev, max_chars):
    """entries: [(key, [att_keys], title)]. Keeps out-of-band items listed in `prev`;
    items `prev` lists but `entries` lacks (left the collection) are re-extracted by
    their recorded attachment rather than dropped."""
    seen = {k for k, _, _ in entries}
    entries = list(entries) + [(k, [e["att"]] if e.get("att") else [], e.get("title", ""))
                               for k, e in prev.items() if k not in seen]
    manifest, kept = [], []
    for key, atts, title in entries:
        old = prev.get(key)
        if old and old.get("src") not in OWN_SRCS:
            manifest.append(old); kept.append(old)
            continue
        e = extract_item(key, atts, title, max_chars)
        manifest.append(e); _report(e)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    for m in manifest:
        acqlog.record(zotero=m["key"], title=m.get("title"), attachment=m.get("att"),
                      extractor=m.get("extractor") or m.get("src"), pages=m.get("pages"),
                      chars=m.get("chars"), complete=m.get("complete"), flags=m.get("flags"),
                      stored=m.get("pdf") or m.get("html"))
    n_ok = sum(1 for m in manifest if (m.get("size") or 0) > 0)
    bad = [m for m in manifest if m.get("complete") is False]
    print(f"\n{n_ok}/{len(manifest)} items have text extracted → {OUTDIR}")
    for m in kept:
        print(f"  kept, placed by another extractor ({m.get('src')}): {m['key']} {m.get('title', '')[:60]}")
    for m in bad:
        print(f"  INCOMPLETE {m['key']}: {'; '.join(m.get('flags', []))}")

def _load_manifest(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {e["key"]: e for e in json.load(f)}

def _max_chars(argv):
    if "--max-chars" in argv:
        i = argv.index("--max-chars")
        n = int(argv[i + 1]); del argv[i:i + 2]
        return n
    return None

def main(argv):
    max_chars = _max_chars(argv)
    if not argv:
        sys.exit(__doc__)
    from zotero_mcp import MCP, result_json
    ck = argv[0]
    os.makedirs(OUTDIR, exist_ok=True)
    mcp = MCP("extract", throttle=0.2)
    coll = result_json(mcp.call("get_collection_items", {"collectionKey": ck, "limit": 500}))
    items = coll.get("data") if isinstance(coll, dict) else None
    if items is None and isinstance(coll, dict): items = coll.get("items", [])
    if items is None: items = coll
    entries = []
    for it in items:
        d = it.get("data", it)
        key = d.get("key") or it.get("key")
        title = (d.get("title") or "")[:80]
        ch = result_json(mcp.call("get_item_details", {"itemKey": key, "mode": "standard"}))
        atts = (ch or {}).get("attachments", []) if isinstance(ch, dict) else []
        if not atts:
            data = (ch or {}).get("data", ch) if isinstance(ch, dict) else None
            atts = (data or {}).get("attachments", [])
        atts = [a.get("key") if isinstance(a, dict) else a for a in atts]
        entries.append((key, [a for a in atts if a], title))
    mpath = os.path.join(DISC, f"text_manifest_{ck}.json")
    _run(entries, mpath, _load_manifest(mpath), max_chars)

def cmd_refresh(argv):
    """Re-extract the items an existing manifest lists, resolving each by its
    recorded attachment key — no Zotero API needed. Output goes to the `text/`
    beside the manifest."""
    global DISC, OUTDIR
    max_chars = _max_chars(argv)
    if len(argv) != 1:
        sys.exit("usage: extract_texts.py refresh <text_manifest.json> [--max-chars N]")
    mpath = os.path.abspath(argv[0])
    DISC = os.path.dirname(mpath); OUTDIR = os.path.join(DISC, "text")
    os.makedirs(OUTDIR, exist_ok=True)
    prev = _load_manifest(mpath)
    entries = [(k, [e["att"]] if e.get("att") else [], e.get("title", "")) for k, e in prev.items()]
    _run(entries, mpath, prev, max_chars)

def _mixed_share(text):
    w = re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text)
    bad = sum(1 for x in w if re.search(r"[А-Яа-яЁё]", x) and re.search(r"[A-Za-z]", x))
    return bad / max(len(w), 1)

def cmd_check(argv):
    """Audit a text dir against its manifest. A file fails when it has no page
    marks (flat legacy extraction), fewer page marks than the manifest's page
    count, fewer characters than the manifest recorded, an excerpt flag, or when
    many files share one exact length (the signature of a silent cap)."""
    import argparse, collections
    ap = argparse.ArgumentParser(prog="extract_texts.py check")
    ap.add_argument("textdir")
    ap.add_argument("--manifest", help="default: the text_manifest*.json beside the dir")
    a = ap.parse_args(argv)
    mpath = a.manifest
    if not mpath:
        c = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(a.textdir)), "text_manifest*.json")))
        mpath = c[0] if len(c) == 1 else None
    man = _load_manifest(mpath) if mpath else {}
    files = sorted(glob.glob(os.path.join(a.textdir, "*.txt")))
    texts = {}
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            texts[os.path.basename(f)[:-4]] = fh.read()
    lens = collections.Counter(len(t) for t in texts.values())
    rows, n_fail = [], 0
    for key, t in texts.items():
        e = man.get(key, {})
        marks = [int(m) for m in PAGE_MARK.findall(t)]
        why = []
        if e.get("src", "pdftotext") == "pdftotext":   # paged PDF text (flat sources carry no marks)
            if not marks:
                why.append("no page marks")
            elif e.get("pages") is not None and len(marks) < e["pages"]:
                why.append(f"{len(marks)}/{e['pages']} pages")
        if e.get("size") and e.get("src") in OWN_SRCS and len(t) < e["size"]:
            why.append(f"{len(t)} < manifest {e['size']} chars")
        if e.get("truncated"):
            why.append("excerpt")
        if lens[len(t)] >= 3 and len(t) > 1000:
            why.append(f"{lens[len(t)]} files share length {len(t)}")
        if e.get("complete") is False:
            why += [f for f in e.get("flags", []) if f not in why]
        cpp = len(t) // max(len(marks), 1) if marks else None
        rows.append((key, len(marks) or "-", len(t), cpp or "-", f"{_mixed_share(t)*100:.1f}%", "; ".join(why)))
        n_fail += bool(why)
    rows.sort(key=lambda r: (not r[5], r[0]))
    print(f"{'key':<10} {'pages':>5} {'chars':>9} {'c/page':>7} {'mixed':>6}  problem")
    for r in rows:
        print(f"{r[0]:<10} {r[1]:>5} {r[2]:>9} {r[3]:>7} {r[4]:>6}  {r[5]}")
    print(f"\n{len(rows) - n_fail}/{len(rows)} files pass" + (f" (manifest {os.path.basename(mpath)})" if mpath else " (no manifest)"))
    sys.exit(1 if n_fail else 0)

if __name__=="__main__":
    cmds = {"md": cmd_md, "refresh": cmd_refresh, "check": cmd_check}
    if len(sys.argv) >= 2 and sys.argv[1] in cmds:
        cmds[sys.argv[1]](sys.argv[2:])
    else:
        main(sys.argv[1:])

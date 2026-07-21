#!/usr/bin/env python3
"""Extract text/markdown from PDFs, two ways:

1. Zotero-collection mode (default): walk a Zotero collection. For every parent
   item with a child PDF attachment, extract text via Zotero's pre-cached
   `.zotero-ft-cache` (Zotero indexes PDFs on import) or via `pdftotext` as a
   fallback. Save to <DISCOVERY_OUT>/text/<itemKey>.txt plus a JSON manifest.

2. Markdown mode (`md` subcommand): pure-python, no GPU/OCR. Convert one or more
   PDFs (or every *.pdf in a directory) to Markdown via pymupdf4llm's text-layer
   reader, emitting the same `{N}`+48-dash paginated format the marker pipeline
   produces. Because it reads the embedded text layer (not OCR), it avoids
   marker's homoglyph errors (Latin acronyms mis-rendered as Cyrillic, dropped words),
   so its output is a clean reference for diffing/patching marker `.md` files.

Usage:
  extract_texts.py <collectionKey>                 # Zotero-collection mode
  extract_texts.py md <pdf-or-dir> [...] [--out DIR] [--suffix .txt] [--stdout]

Env (collection mode only):
  ZOTERO_MCP_TOKEN  required for collection mode.
  ZOTERO_STORAGE    path to Zotero `storage/` dir (default ~/zotero-setup/config/Zotero/storage).
  DISCOVERY_OUT     output dir parent (default ~/zotero-setup/.discovery).
"""
import json, os, sys, subprocess, glob

from zotero_mcp import MCP, result_json

SEP_DASHES = 48  # marker-compatible page rule width
ZSTORE  = os.environ.get("ZOTERO_STORAGE",
                         os.path.expanduser("~/zotero-setup/config/Zotero/storage"))
DISC    = os.environ.get("DISCOVERY_OUT", os.path.expanduser("~/zotero-setup/.discovery"))
OUTDIR  = os.path.join(DISC, "text")

os.makedirs(OUTDIR, exist_ok=True)

def find_pdf(att_key):
    """Look for a PDF in the attachment's storage dir."""
    d = os.path.join(ZSTORE, att_key)
    if not os.path.isdir(d): return None
    pdfs = glob.glob(os.path.join(d, "*.pdf"))
    return pdfs[0] if pdfs else None

def find_ft_cache(att_key):
    d = os.path.join(ZSTORE, att_key)
    p = os.path.join(d, ".zotero-ft-cache")
    return p if os.path.isfile(p) else None

def _write_if_nonempty(out_path, text):
    """Write text to out_path, but never clobber an existing non-empty file with empty content."""
    text = (text or "")
    if not text.strip():
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            print(f"  WARN: preserving existing {os.path.basename(out_path)} ({os.path.getsize(out_path)} bytes); new content empty", file=sys.stderr)
            return os.path.getsize(out_path)
        return 0
    with open(out_path, "w") as f:
        f.write(text)
    return len(text)

def _pdftotext(pdf):
    """Plain text via poppler's pdftotext binary. Raises if the binary is absent."""
    return subprocess.check_output(["pdftotext", "-layout", "-q", pdf, "-"],
                                   stderr=subprocess.DEVNULL, timeout=30).decode("utf-8", "replace")

def _pymupdf_text(pdf):
    """Pure-python fallback when pdftotext is unavailable (uses the same pymupdf
    dependency the `md` mode already relies on). Reads the embedded text layer."""
    import pymupdf
    doc = pymupdf.open(pdf)
    try:
        return "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()

def extract_pdf_text(pdf, out_path, max_chars=20000):
    """Extract first N chars of a PDF's text layer. Prefers pdftotext; falls back to
    pymupdf when the binary is missing. Never clobbers an existing non-empty .txt."""
    import re
    text = None
    try:
        text = _pdftotext(pdf)
    except Exception:
        try:
            text = _pymupdf_text(pdf)
        except Exception:
            return -1
    text = re.sub(r'\s+', ' ', text or "").strip()
    return _write_if_nonempty(out_path, text[:max_chars])

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
        with open(out_path, "w") as f:
            f.write(md)
        n_ok += 1
        print(f"  {os.path.basename(pdf)} -> {out_path} ({len(md)} chars)")
    print(f"\n{n_ok}/{len(targets)} PDFs converted to markdown")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: extract_texts.py <collectionKey>  |  extract_texts.py md <pdf-or-dir> ...")
    ck = sys.argv[1]
    mcp = MCP("extract", throttle=0.2)
    coll = result_json(mcp.call("get_collection_items", {"collectionKey": ck, "limit": 200}))
    items = coll.get("data") if isinstance(coll, dict) else None
    if items is None and isinstance(coll, dict): items = coll.get("items", [])
    if items is None: items = coll
    manifest = []
    for it in items:
        d = it.get("data", it)
        key = d.get("key") or it.get("key")
        title = (d.get("title") or "")[:80]
        # Get children
        ch_resp = mcp.call("get_item_details", {"itemKey": key, "mode":"standard"})
        ch = result_json(ch_resp)
        # Attachments are listed in 'attachments' key
        atts = (ch or {}).get("attachments", []) if isinstance(ch, dict) else []
        if not atts:
            data = (ch or {}).get("data", ch) if isinstance(ch, dict) else None
            atts = (data or {}).get("attachments", [])
        pdf_path = None
        att_key = None
        for a in atts:
            ak = a.get("key") if isinstance(a, dict) else a
            if not ak: continue
            p = find_pdf(ak)
            if p:
                pdf_path = p; att_key = ak; break
        # Fallback: glob the whole storage tree for any PDF that's a child of this parent
        if not pdf_path:
            # Try ft-cache instead
            for a in atts:
                ak = a.get("key") if isinstance(a, dict) else a
                if not ak: continue
                ft = find_ft_cache(ak)
                if ft:
                    out_path = os.path.join(OUTDIR, f"{key}.txt")
                    with open(ft) as f:
                        content = f.read()[:20000]
                    n = _write_if_nonempty(out_path, content)
                    if n > 0:
                        manifest.append({"key": key, "src":"ft-cache", "att": ak, "title": title, "size": n})
                        print(f"  {key}: ft-cache via {ak}")
                        break
            else:
                # No PDF and no usable ft-cache. Preserve any existing .txt (may have been placed
                # manually by an out-of-band extractor such as docx2txt for SocArXiv .docx files).
                out_path = os.path.join(OUTDIR, f"{key}.txt")
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    manifest.append({"key": key, "src":"external", "att": None, "title": title, "size": os.path.getsize(out_path)})
                    print(f"  {key}: kept external text ({os.path.getsize(out_path)} bytes)")
                else:
                    manifest.append({"key": key, "src":"none", "att": None, "title": title, "size": 0})
                    print(f"  {key}: NO PDF FOUND  '{title}'")
            continue
        out_path = os.path.join(OUTDIR, f"{key}.txt")
        n = extract_pdf_text(pdf_path, out_path)
        manifest.append({"key": key, "src":"pdftotext", "att": att_key, "pdf": pdf_path, "title": title, "size": n if n>0 else 0})
        print(f"  {key}: {n} chars from {pdf_path}")
    with open(os.path.join(DISC, f"text_manifest_{ck}.json"),"w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    n_ok = sum(1 for m in manifest if m["size"]>0)
    print(f"\n{n_ok}/{len(manifest)} items have text extracted → {OUTDIR}")

if __name__=="__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "md":
        cmd_md(sys.argv[2:])
    else:
        main()

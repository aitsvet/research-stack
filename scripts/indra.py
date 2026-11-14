#!/usr/bin/env python3
"""Full-document OCR on the CPU, no GPU and no server -> paginated Markdown.

Indra OCR finds the lines of a page, labels its layout blocks and reads each
line, reporting a confidence per line. It is not a vision-language model, so it
does not rewrite the page: it misreads a character or drops a line, where a
generative reader can substitute a word that looks alike and mean something
else. That and a laptop-sized model (~76 MB of weights, ONNX Runtime, no
PyTorch) make it the route that still works when no Chandra endpoint is up, and
the cheap first pass over clean print when one is.

Output is one `.md` per input in the corpus pagination (`{N}` + 48 dashes, page
numbers from 0), the same shape `chandra_ocr.py` writes, so the page-count
check, `extract_tables.py`, `corpus_index.py` and `pdf_text_layer.py` take it
unchanged. A page nothing can be read from becomes `*[OCR FAILED FOR THIS
PAGE]*`, the marker the corpus audit greps for.

    indra.py <pdf|image> [--out F] [--mode auto|ocr|text] [--dpi 200]
             [--math] [--furniture] [--threads N] [--model-dir D]
             [--flag-conf X] [--json]

`--mode auto` (default) reads a PDF's text layer where it passes the model's own
checks and OCRs only the rest, including text inside images; `ocr` never trusts
the layer; `text` always does. `--flag-conf` lists the pages whose mean line
confidence falls below the threshold — the pages to re-run through
`chandra_ocr.py` where accuracy matters. `--json` writes the boxes, block labels
and timings beside the Markdown for an audit that needs coordinates.

Handwriting, degraded scans, tables with merged cells, diagrams and formulas
outside `--math` are what it does badly; that is Chandra's work, not this.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import acqlog
from html_md import tables_to_md
from pagemd import mark

FAILED = "*[OCR FAILED FOR THIS PAGE]*"


def _engine():
    try:
        import indra_ocr
    except ImportError:
        sys.exit("indra-ocr is not installed (CPU only, no torch): "
                 "pip install indra-ocr")
    if not hasattr(indra_ocr, "load"):
        # A file of the same name beside this one would shadow the package.
        sys.exit(f"the module imported as indra_ocr is not the package "
                 f"({getattr(indra_ocr, '__file__', 'unknown source')})")
    return indra_ocr


def _field(obj, name, default=None):
    """A page or line attribute whether the object is a record or a mapping."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _conf(line):
    try:
        return round(float(_field(line, "conf")), 4)
    except (TypeError, ValueError):
        return None


def _lines(page):
    out = []
    for line in _field(page, "lines") or []:
        box = _field(line, "box")
        out.append({"box": [round(float(v), 1) for v in box] if box else None,
                    "text": _field(line, "text") or "",
                    "conf": _conf(line)})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PDF or image (PNG, JPEG, TIFF, BMP, WebP)")
    ap.add_argument("--out", default=None, help="output .md (default: <input>.md)")
    ap.add_argument("--mode", default="auto", choices=["auto", "ocr", "text"],
                    help="text layer handling (default: auto)")
    ap.add_argument("--dpi", type=int, default=200,
                    help="render resolution for PDF pages (default: 200)")
    ap.add_argument("--math", action="store_true",
                    help="read math lines as LaTeX (slower, and some table cells "
                         "come out as math)")
    ap.add_argument("--furniture", action="store_true",
                    help="keep page headers and footers")
    ap.add_argument("--threads", type=int, default=0,
                    help="ONNX threads (default: the model's own choice)")
    ap.add_argument("--model-dir", default=os.environ.get("INDRA_OCR_MODEL_DIR", ""),
                    help="local model folder (default: download once and cache)")
    ap.add_argument("--flag-conf", type=float, default=0.0,
                    help="list pages whose mean line confidence is below X")
    ap.add_argument("--json", action="store_true",
                    help="also write <out>.jsonl with line boxes, labels and timings")
    a = ap.parse_args()

    engine = _engine()
    out_path = a.out or (os.path.splitext(a.input)[0] + ".md")
    load = {"dpi": a.dpi, "mode": a.mode, "math": a.math, "furniture": a.furniture}
    if a.threads:
        load["threads"] = a.threads
    if a.model_dir:
        load["path"] = a.model_dir
    conv = engine.load(**load)

    reader_error = getattr(engine, "InputError", None)
    print(f"[indra] {a.input} -> {out_path}", file=sys.stderr)

    pages_md, rows, low = [], [], []
    try:
        stream = conv.convert(a.input)
        for pos, item in enumerate(stream):
            # indra_ocr 1.1 yields (page index, Page); earlier builds yielded the
            # Page alone. Take the index from the tuple so the marks keep the
            # source page numbers either way.
            i, page = item if isinstance(item, tuple) else (pos, item)
            # Indra answers Markdown with HTML tables inside it; the corpus stores
            # pipe tables so the table tooling can read them.
            body = (tables_to_md(_field(page, "markdown") or "")).strip()
            if not body:
                body = (_field(page, "text") or "").strip()
            if not body or _field(page, "source") == "error":
                body = FAILED
            pages_md.append(mark(i) + "\n\n" + body)

            lines = _lines(page)
            confs = [l["conf"] for l in lines if l["conf"] is not None]
            mean = sum(confs) / len(confs) if confs else None
            if mean is not None and 0 < mean < a.flag_conf:
                low.append((i + 1, mean))
            turned = _field(page, "rotation") or _field(page, "skew")
            if turned:
                print(f"[indra] page {i + 1}: turned {turned}", file=sys.stderr)
            if a.json:
                rows.append({"page": i,
                             "source": _field(page, "source") or "",
                             "rotation": _field(page, "rotation", 0),
                             "skew": _field(page, "skew", 0),
                             "note": _field(page, "note") or "",
                             "timings": _field(page, "timings") or {},
                             "mean_conf": None if mean is None else round(mean, 4),
                             "blocks": _field(page, "blocks") or [],
                             "lines": lines})
    except Exception as e:  # noqa: BLE001 — a bad file must not be a traceback
        if reader_error is not None and isinstance(e, reader_error):
            sys.exit(f"{a.input}: cannot be read ({e})")
        raise
    if not pages_md:
        sys.exit(f"{a.input}: no pages read")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n\n" + "\n\n".join(pages_md) + "\n")
    if a.json:
        with open(out_path + ".jsonl", "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    failed = sum(FAILED in p for p in pages_md)
    print(f"[indra] wrote {out_path} ({len(pages_md)} pages, "
          f"{failed} failed)", file=sys.stderr)
    if low:
        pages_list = ", ".join(f"{n} ({c:.2f})" for n, c in low)
        print(f"[indra] low confidence, worth a Chandra pass: {pages_list}",
              file=sys.stderr)
    acqlog.record(path=a.input, extractor="indra", text=out_path,
                  pages=len(pages_md), ocr_failed=failed or None,
                  flags=["low confidence page(s): " +
                         ", ".join(str(n) for n, _ in low)] if low else None)


if __name__ == "__main__":
    main()

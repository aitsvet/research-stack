#!/usr/bin/env python3
"""Losslessly strip white background margins from every page of a PDF.

For each page we render a low-res grayscale raster, find the bounding box of
the non-white content via row/column projections (speckle-robust), then set the
page CropBox to that box (plus a small padding). Only the visible box changes —
the underlying page/image objects are untouched, so this is lossless: no
re-rendering or re-compression of scanned images. Renderers, OCR, and Zotero
previews then show tight content instead of a white A4 border.

Domain-agnostic: takes any PDF in, writes any PDF out.

Usage:
    pdf_trim_margins.py <in.pdf> <out.pdf> [options]

Options:
    --dpi N          detection render resolution (default 150)
    --threshold N    gray value 0-255; pixels darker than this are "content"
                     (default 245, i.e. anything not near-white)
    --min-frac F     a row/column counts as content only if the fraction of
                     content pixels in it is >= F (ignores scan speckle;
                     default 0.004)
    --pad-pt P       padding kept around detected content, in PDF points
                     (default 8)
    --quiet          suppress per-page logging
"""
import argparse
import sys

import fitz  # PyMuPDF
import numpy as np


def content_bbox_px(gray, threshold, min_frac):
    """Return (x0, y0, x1, y1) pixel bbox of content, or None if page blank."""
    mask = gray < threshold  # True where dark enough to be content
    h, w = mask.shape
    col = mask.sum(axis=0)  # content-pixel count per column
    row = mask.sum(axis=1)  # content-pixel count per row
    cols = np.where(col >= max(1, int(min_frac * h)))[0]
    rows = np.where(row >= max(1, int(min_frac * w)))[0]
    if cols.size == 0 or rows.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def trim(in_pdf, out_pdf, dpi, threshold, min_frac, pad_pt, quiet):
    doc = fitz.open(in_pdf)
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    trimmed = 0
    for i, page in enumerate(doc):
        if page.rotation:
            # Cropbox math below assumes unrotated coords; leave rotated pages be.
            if not quiet:
                print(f"  page {i + 1}: rotation={page.rotation}, skipped", file=sys.stderr)
            continue
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        box = content_bbox_px(gray, threshold, min_frac)
        if box is None:
            if not quiet:
                print(f"  page {i + 1}: blank, kept full", file=sys.stderr)
            continue
        x0, y0, x1, y1 = (v / scale for v in box)  # px -> points
        # pad and clamp to the existing mediabox
        mb = page.mediabox
        rect = fitz.Rect(
            max(mb.x0, x0 - pad_pt),
            max(mb.y0, y0 - pad_pt),
            min(mb.x1, x1 + pad_pt),
            min(mb.y1, y1 + pad_pt),
        )
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            continue
        page.set_cropbox(rect)
        trimmed += 1
        if not quiet:
            pct = 100 * (rect.width * rect.height) / (mb.width * mb.height)
            print(f"  page {i + 1}: {mb.width:.0f}x{mb.height:.0f} -> "
                  f"{rect.width:.0f}x{rect.height:.0f}pt ({pct:.0f}% of page)",
                  file=sys.stderr)
    doc.save(out_pdf, garbage=4, deflate=True)
    doc.close()
    print(f"[trim] {trimmed}/{i + 1} pages cropped -> {out_pdf}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("in_pdf")
    ap.add_argument("out_pdf")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--threshold", type=int, default=245)
    ap.add_argument("--min-frac", type=float, default=0.004)
    ap.add_argument("--pad-pt", type=float, default=8.0)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    trim(a.in_pdf, a.out_pdf, a.dpi, a.threshold, a.min_frac, a.pad_pt, a.quiet)


if __name__ == "__main__":
    main()

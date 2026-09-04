#!/usr/bin/env python3
"""Split a PDF laid out as two-page spreads into one book page per PDF page.

Scanned and print-ready books are often imposed two book pages to a sheet. Every
downstream tool then reads a sheet as one page: `extract_texts.py md` interleaves
the two columns, `chandra_ocr.py` OCRs a spread into one blob, and the `{N}`
marks count sheets rather than book pages, so nothing lines up with the numbers
printed in the book.

This normalises the input instead of duplicating extraction logic: each sheet is
emitted twice with a /CropBox over its left and right half, losslessly (content
streams are untouched, only the box changes). Feed the result to the usual
pipeline and the pages, the marks and the folios agree.

Rotation is handled through the page's own matrices rather than by hand, so
/Rotate 90/180/270 spreads (common when a portrait book is imposed on landscape
sheets) split correctly.

The spine is found per sheet as the widest text-free vertical band near the
middle, which tolerates uneven inner margins; pass `--spine 0.5` to force the
geometric centre when a sheet is too sparse to measure (plates, blank versos).

Covers and inserts are frequently single pages on their own sheet — list them
with `--single` to copy them through whole.

Usage:
  split_spreads.py <in.pdf> <out.pdf> [--spine auto|FRACTION]
                   [--single 1,240] [--dry-run] [--quiet]
"""
import argparse
import sys

import fitz  # PyMuPDF

BINS = 400          # horizontal resolution for gutter detection
MIDDLE = 0.30       # spine is searched within this fraction around the centre


def display_lines(page):
    """Text line rectangles in the page's displayed (rotated) coordinates.

    get_text() reports boxes in unrotated page space; page.rotation_matrix maps
    them onto what the reader actually sees.

    Lines, not blocks: PyMuPDF routinely groups the two running heads of a
    spread into a single block spanning the gutter, which would hide the very
    gap being measured.
    """
    out = []
    for bl in page.get_text("dict")["blocks"]:
        for ln in bl.get("lines", ()):
            if not any(s["text"].strip() for s in ln["spans"]):
                continue
            r = fitz.Rect(ln["bbox"]) * page.rotation_matrix
            r.normalize()
            if r.width > 0 and r.height > 0:
                out.append(r)
    return out


def find_spine(page):
    """Widest text-free vertical band near the middle, as a fraction of width.

    Returns None when the sheet carries too little text to measure.
    """
    W = page.rect.width
    if W <= 0:
        return None
    blocks = display_lines(page)
    if len(blocks) < 2:
        return None

    covered = [False] * BINS
    for r in blocks:
        lo = max(0, int(r.x0 / W * BINS))
        hi = min(BINS - 1, int(r.x1 / W * BINS))
        for i in range(lo, hi + 1):
            covered[i] = True

    lo_b, hi_b = int(BINS * (0.5 - MIDDLE / 2)), int(BINS * (0.5 + MIDDLE / 2))
    best, run_start = None, None
    for i in range(lo_b, hi_b + 1):
        if not covered[i]:
            run_start = i if run_start is None else run_start
        elif run_start is not None:
            if best is None or i - run_start > best[1] - best[0]:
                best = (run_start, i)
            run_start = None
    if run_start is not None and (best is None or hi_b + 1 - run_start > best[1] - best[0]):
        best = (run_start, hi_b + 1)
    if best is None:
        return None
    return (best[0] + best[1]) / 2 / BINS


def half_crop(page, frac, side):
    """CropBox (unrotated coords) for one half of the displayed page."""
    r = page.rect
    x = r.x0 + r.width * frac
    half = fitz.Rect(r.x0, r.y0, x, r.y1) if side == "L" else fitz.Rect(x, r.y0, r.x1, r.y1)
    crop = half * page.derotation_matrix
    crop.normalize()
    # keep inside the mediabox; a stray fraction of a point is refused outright
    return crop & page.mediabox


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("in_pdf")
    ap.add_argument("out_pdf")
    ap.add_argument("--spine", default="auto",
                    help="'auto' (default) or a fraction of page width, e.g. 0.5")
    ap.add_argument("--single", default="",
                    help="1-based sheets to copy whole instead of splitting, "
                         "e.g. covers: --single 1,240")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the detected spine per sheet, write nothing")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    singles = {int(x) for x in a.single.replace(" ", "").split(",") if x}
    forced = None if a.spine == "auto" else float(a.spine)
    if forced is not None and not 0.05 < forced < 0.95:
        sys.exit("--spine must be a fraction strictly between 0.05 and 0.95")

    src = fitz.open(a.in_pdf)
    out = fitz.open()
    measured, severed, fallbacks, kept = [], [], 0, 0

    for i, page in enumerate(src):
        if i + 1 in singles:
            out.insert_pdf(src, from_page=i, to_page=i)
            kept += 1
            continue
        frac = forced if forced is not None else find_spine(page)
        if frac is None:
            frac = 0.5
            fallbacks += 1
        else:
            measured.append(frac)

        # The only failure that matters is a cut running through text; the
        # exact position of the spine inside an empty gutter does not.
        x = page.rect.x0 + page.rect.width * frac
        cut = [r for r in display_lines(page) if r.x0 < x - 0.5 and r.x1 > x + 0.5]
        if cut:
            severed.append((i + 1, len(cut)))

        for side in ("L", "R"):
            out.insert_pdf(src, from_page=i, to_page=i)
            out[-1].set_cropbox(half_crop(page, frac, side))
        if a.dry_run and not a.quiet:
            print(f"  sheet {i + 1}: spine at {frac:.3f} of width "
                  f"({page.rect.width:.0f}x{page.rect.height:.0f}pt, "
                  f"/Rotate={page.rotation}, {len(cut)} line(s) severed)",
                  file=sys.stderr)

    if measured:
        print(f"spine: median {sorted(measured)[len(measured) // 2]:.3f} of width, "
              f"range {min(measured):.3f}..{max(measured):.3f}", file=sys.stderr)
    if fallbacks:
        print(f"{fallbacks} sheet(s) too sparse to measure, split at 0.5",
              file=sys.stderr)
    if severed:
        worst = sorted(severed, key=lambda s: -s[1])[:8]
        print(f"WARNING: spine crosses text on {len(severed)} sheet(s) — "
              f"worst {worst}; check a render and consider --spine",
              file=sys.stderr)

    print(f"{src.page_count} sheets -> {out.page_count} pages "
          f"({kept} copied whole)", file=sys.stderr)
    if a.dry_run:
        return
    out.save(a.out_pdf, garbage=3, deflate=True)
    if not a.quiet:
        print(f"wrote {a.out_pdf}", file=sys.stderr)


if __name__ == "__main__":
    main()

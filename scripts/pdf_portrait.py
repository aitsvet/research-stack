#!/usr/bin/env python3
"""Force pages of a PDF to portrait orientation via the page /Rotate flag.

For scanned books where some pages were photographed sideways, the page box is
landscape (width > height) and the text runs vertically. Setting the page
/Rotate makes viewers (and PDF.js/Zotero, pdftoppm, PyMuPDF) display the page
upright + portrait — losslessly: the scanned image stream is untouched, only the
/Rotate attribute changes. Portrait pages are left as-is.

Direction note: `--angle` is the clockwise rotation applied to landscape pages.
For book scans turned a consistent way this is uniform (90 or 270); verify once
on a render. Use --all to rotate every page (e.g. a wholly-sideways scan).

Usage: pdf_portrait.py <in.pdf> <out.pdf> [--angle 90] [--all] [--quiet]
"""
import argparse
import sys

import fitz  # PyMuPDF


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("in_pdf")
    ap.add_argument("out_pdf")
    ap.add_argument("--angle", type=int, default=90, choices=[90, 180, 270],
                    help="clockwise degrees to apply to landscape pages (default 90)")
    ap.add_argument("--all", action="store_true",
                    help="rotate every page, not just landscape ones")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    doc = fitz.open(a.in_pdf)
    n = 0
    for i, pg in enumerate(doc):
        landscape = pg.rect.width > pg.rect.height
        if a.all or landscape:
            pg.set_rotation((pg.rotation + a.angle) % 360)
            n += 1
            if not a.quiet:
                r = pg.rect  # rect reflects the applied rotation
                print(f"  p{i + 1}: +{a.angle} -> /Rotate={pg.rotation}, "
                      f"displays {r.width:.0f}x{r.height:.0f}pt", file=sys.stderr)
    doc.save(a.out_pdf, garbage=3, deflate=True)
    doc.close()
    print(f"[portrait] rotated {n}/{i + 1} pages -> {a.out_pdf}", file=sys.stderr)


if __name__ == "__main__":
    main()

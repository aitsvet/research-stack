#!/usr/bin/env python3
"""Deck linter: machine checks for the defect classes that otherwise cost
hours of hand alignment. Errors exit non-zero; run before any visual QA.

PPTX never clips text, so frame-box arithmetic lies. Every check here works
on the RENDERED TEXT BBOX — explicit break segments wrapped at inner width
with real TTF metrics, positioned by the frame's anchor.

  lint.py deck.pptx [--eps 0.02] [--max-sizes 8] [--strict] [--slide N]

ERROR classes
  E-align    same-class sibling blocks in a row with unequal tops/heights
  E-collide  rendered text intersects another frame's rendered text
  E-bounds   rendered text extends beyond the slide edge
WARN classes
  W-pic      rendered text intersects a picture (may be its quiet zone)
  W-stagger  equal-size text on one row misaligned by a few px
  W-center   top-anchored text floated inside a much taller filled shape
             (centering faked by arithmetic instead of anchor=ctr)
  W-autofit  normAutofit present (viewer may silently rescale fonts)
  W-tokens   font-size inventory exceeds budget / quarter-point px sizes
  W-drift    near-identical picture sizes drifting across slides
"""
import argparse
import sys
from collections import defaultdict

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

import measure

IN = lambda v: Emu(v).inches if v is not None else None
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def para_segments(p):
    """Paragraph text split on explicit <a:br/> into hard-line segments."""
    segs, cur = [], []
    for child in p._p:
        if child.tag == A + "br":
            segs.append("".join(cur))
            cur = []
        elif child.tag == A + "r":
            cur.append("".join(t.text or "" for t in child.findall(A + "t")))
    segs.append("".join(cur))
    return [s for s in (seg.strip() for seg in segs) if s]


def para_style(p, fallback_pt=18.0):
    pts = [r.font.size.pt for r in p.runs if r.font.size]
    fonts = [r.font.name for r in p.runs if r.font.name]
    bold = any(r.font.bold for r in p.runs)
    pt = max(pts) if pts else fallback_pt
    ls = p.line_spacing
    if ls is None:
        spacing = 1.15
    elif isinstance(ls, float):
        spacing = ls
    else:
        spacing = ls.pt / pt
    return pt, (fonts[0] if fonts else "Arial"), bold, spacing


class Frame:
    """A shape plus the measured bbox of its rendered text."""

    def __init__(self, sh, slide_no):
        self.sh, self.slide = sh, slide_no
        self.x, self.y = IN(sh.left) or 0.0, IN(sh.top) or 0.0
        self.w, self.h = IN(sh.width) or 0.0, IN(sh.height) or 0.0
        self.is_pic = sh.shape_type == MSO_SHAPE_TYPE.PICTURE
        self.tf = sh.text_frame if sh.has_text_frame else None
        self.text = self.tf.text.strip() if self.tf else ""
        self.filled = False
        try:
            self.filled = sh.fill.type is not None and str(sh.fill.type) != "MSO_FILL_TYPE.BACKGROUND"
        except (AttributeError, TypeError):
            pass
        self.pt = None
        self.txt_box = None  # (x0, y0, x1, y1) of rendered text
        if self.text:
            self._measure()

    def _measure(self):
        tf = self.tf
        ml = IN(tf.margin_left) if tf.margin_left is not None else 0.1
        mr = IN(tf.margin_right) if tf.margin_right is not None else 0.1
        mt = IN(tf.margin_top) if tf.margin_top is not None else 0.05
        inner_w = max(self.w - ml - mr, 0.3)
        total_h, max_w, pts = 0.0, 0.0, []
        centered = True
        for p in tf.paragraphs:
            if not p.runs:
                continue
            pt, font, bold, spacing = para_style(p)
            pts.append(pt)
            if str(p.alignment) not in ("PP_ALIGN.CENTER (2)",):
                centered = False
            for seg in para_segments(p):
                lines = measure.wrap(seg, font, pt, inner_w, bold) if tf.word_wrap is not False else [seg]
                total_h += len(lines) * measure.line_height_in(pt, spacing)
                max_w = max(max_w, *(measure.text_width_in(l, font, pt, bold) for l in lines))
            if p.space_after is not None:
                total_h += p.space_after.pt / 72
        self.pt = max(pts) if pts else None
        anch = str(tf.vertical_anchor)
        if "MIDDLE" in anch:
            y0 = self.y + (self.h - total_h) / 2
        elif "BOTTOM" in anch:
            y0 = self.y + self.h - total_h
        else:
            y0 = self.y + mt
        x0 = self.x + ml + ((inner_w - max_w) / 2 if centered else 0)
        self.txt_box = (x0, y0, x0 + max_w, y0 + total_h)

    def right(self):
        return self.x + self.w

    def bottom(self):
        return self.y + self.h


def isect(a, b):
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    return (ix, iy) if ix > 0 and iy > 0 else (0.0, 0.0)


def lint(path, eps=0.02, max_sizes=8, only_slide=None):
    prs = Presentation(path)
    SW, SH = IN(prs.slide_width), IN(prs.slide_height)
    issues = []

    def report(code, slide, msg):
        issues.append((code, slide, msg))

    pic_ws, size_ctr = [], defaultdict(int)
    for i, slide in enumerate(prs.slides, 1):
        if only_slide and i != only_slide:
            continue
        frames = [Frame(sh, i) for sh in slide.shapes]
        texted = [f for f in frames if f.txt_box]
        cards = [f for f in frames if f.filled and not f.text
                 and f.w < 0.9 * SW and f.h < 0.9 * SH]
        pics = [f for f in frames if f.is_pic]
        pic_ws += [round(f.w, 3) for f in pics]

        for t in texted:
            for p in t.tf.paragraphs:
                for r in p.runs:
                    if r.font.size:
                        size_ctr[round(r.font.size.pt, 2)] += 1
            if t.tf._txBody.bodyPr.find(A + "normAutofit") is not None:
                report("W-autofit", i, f"normAutofit on {t.text[:30]!r}")
            x0, y0, x1, y1 = t.txt_box
            if x1 > SW + eps or x0 < -eps or y0 < -eps or y1 > SH + eps:
                report("E-bounds", i, f"text {t.text[:30]!r} rendered bbox "
                                      f"({x0:.2f},{y0:.2f})-({x1:.2f},{y1:.2f}) leaves the slide")

        # rendered-text collisions
        for n, t in enumerate(texted):
            for o in texted[n + 1:]:
                ix, iy = isect(t.txt_box, o.txt_box)
                if ix > 0.08 and iy > 0.08:
                    report("E-collide", i, f"text {t.text[:25]!r} and {o.text[:25]!r} "
                                           f"overlap {ix:.2f}x{iy:.2f}\"")
            for o in pics:
                ix, iy = isect(t.txt_box, (o.x, o.y, o.right(), o.bottom()))
                if ix > 0.08 and iy > 0.08:
                    report("W-pic", i, f"text {t.text[:25]!r} over picture by {ix:.2f}x{iy:.2f}\"")

        # rows of same-class cards must share top and height
        for n, a in enumerate(cards):
            for b in cards[n + 1:]:
                same_class = abs(a.w - b.w) < 0.6 and abs(a.h - b.h) < 0.6
                overlap_v = min(a.bottom(), b.bottom()) - max(a.y, b.y)
                if same_class and overlap_v > 0.5 * min(a.h, b.h):
                    if abs(a.y - b.y) > eps or abs(a.h - b.h) > eps:
                        report("E-align", i, f"sibling blocks tops {a.y:.3f}/{b.y:.3f} "
                                             f"heights {a.h:.3f}/{b.h:.3f}")

        # equal-size text sharing a row, staggered by a few px
        for n, a in enumerate(texted):
            for b in texted[n + 1:]:
                if not a.pt or a.pt != b.pt:
                    continue  # different sizes legitimately offset for baselines
                overlap_v = min(a.bottom(), b.bottom()) - max(a.y, b.y)
                if overlap_v > 0.6 * min(a.h, b.h) and eps < abs(a.y - b.y) <= 0.15:
                    report("W-stagger", i, f"{a.text[:20]!r} vs {b.text[:20]!r}: "
                                           f"tops differ {abs(a.y - b.y) * 96:.1f}px")

        # fake centering: top-anchored text floated inside a much taller card
        for t in texted:
            if t.tf.vertical_anchor is not None and "TOP" not in str(t.tf.vertical_anchor):
                continue
            for c in cards:
                inside = c.x - eps <= t.x and t.right() <= c.right() + eps and \
                         c.y - eps <= t.y and t.bottom() <= c.bottom() + eps
                if inside and t.y - c.y > 0.5 and c.bottom() - t.bottom() > 0.3:
                    report("W-center", i, f"{t.text[:30]!r} floated at y+{t.y - c.y:.2f}\" "
                                          f"in {c.h:.2f}\" card with anchor=TOP")

    if len(size_ctr) > max_sizes:
        report("W-tokens", 0, f"{len(size_ctr)} font sizes (budget {max_sizes}): {sorted(size_ctr)}")
    quarter = [s for s in size_ctr if (s * 4) == int(s * 4) and (s * 2) != int(s * 2)]
    if quarter:
        report("W-tokens", 0, f"quarter-point sizes (px*0.75 conversion smell): {sorted(quarter)}")
    for rep, n, spread in _cluster(pic_ws, 0.25):
        if n > 1 and spread > 0.02:
            report("W-drift", 0, f"{n} near-identical pictures drift {spread * 96:.1f}px around {rep:.2f}\"")
    return issues


def _cluster(values, eps):
    out = []
    for v in sorted(values):
        for c in out:
            if abs(c[0] - v) <= eps:
                c[1] += 1
                c[2] = max(c[2], abs(v - c[0]))
                break
        else:
            out.append([v, 1, 0.0])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pptx")
    ap.add_argument("--eps", type=float, default=0.02, help="alignment tolerance, inches")
    ap.add_argument("--max-sizes", type=int, default=8)
    ap.add_argument("--strict", action="store_true", help="warnings also fail")
    ap.add_argument("--slide", type=int)
    a = ap.parse_args()
    issues = lint(a.pptx, a.eps, a.max_sizes, a.slide)
    errors = 0
    for code, slide, msg in sorted(issues, key=lambda t: (t[1], t[0])):
        print(f"{'slide ' + str(slide) if slide else 'deck   '}  {code:9s} {msg}")
        errors += code.startswith("E-") or a.strict
    print(f"-- {len(issues)} findings, {errors} blocking")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

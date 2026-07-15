#!/usr/bin/env python3
"""Observe: derive the implicit design system of an existing .pptx.

Prints per-slide shape geometry plus a cross-slide profile: font-size
inventory, column x-clusters, row pitches, anchor/autofit usage, theme fonts
and colors, and a px-conversion smell check. Use this BEFORE adding slides to
a foreign deck — new slides may only use values from this profile.

  observe.py deck.pptx            # human-readable profile
  observe.py deck.pptx --json     # machine-readable (for lint.py --tokens)
  observe.py deck.pptx --slides   # also dump every shape per slide
"""
import argparse
import json
from collections import Counter, defaultdict

from pptx import Presentation
from pptx.util import Emu

IN = lambda v: round(Emu(v).inches, 3) if v is not None else None


def shape_row(sh):
    row = {
        "x": IN(sh.left), "y": IN(sh.top), "w": IN(sh.width), "h": IN(sh.height),
        "type": str(sh.shape_type), "name": sh.name,
    }
    if sh.has_text_frame:
        tf = sh.text_frame
        sizes, fonts, bolds = set(), set(), set()
        for p in tf.paragraphs:
            for r in p.runs:
                if r.font.size:
                    sizes.add(round(r.font.size.pt, 2))
                if r.font.name:
                    fonts.add(r.font.name)
                bolds.add(bool(r.font.bold))
        row.update(
            text=tf.text.replace("\n", " ")[:80],
            pt=sorted(sizes), fonts=sorted(fonts),
            anchor=str(tf.vertical_anchor),
            autofit=str(tf.auto_size),
            has_norm_autofit=bool(tf._txBody.bodyPr.find(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}normAutofit"
            ) is not None),
        )
    return row


def cluster(values, eps=0.05):
    """Group near-equal values; returns [(representative, count)] by count desc."""
    out = []
    for v in sorted(values):
        for c in out:
            if abs(c[0] - v) <= eps:
                c[1] += 1
                break
        else:
            out.append([v, 1])
    return sorted(([round(v, 3), n] for v, n in out), key=lambda t: -t[1])


def px_smell(sizes, slide_w_in):
    """Signs the deck was converted from a 96dpi pixel canvas."""
    smells = []
    if abs(slide_w_in - 20.0) < 0.01:
        smells.append('slide is 20x11.25" = 1920x1080px @96dpi')
    frac = [s for s in sizes if abs(s * 4 - round(s * 4)) < 1e-6 and s != int(s) and (s * 2) != int(s * 2)]
    if frac:
        smells.append(f"quarter-point font sizes (px*0.75): {sorted(frac)}")
    return smells


def profile(path):
    prs = Presentation(path)
    slides = []
    size_ctr, xs, tops, anchors, autofits = Counter(), [], defaultdict(list), Counter(), 0
    fonts = Counter()
    for slide in prs.slides:
        rows = [shape_row(sh) for sh in slide.shapes]
        slides.append(rows)
        for r in rows:
            if r.get("pt"):
                for s in r["pt"]:
                    size_ctr[s] += 1
                for f in r.get("fonts", []):
                    fonts[f] += 1
                anchors[r["anchor"]] += 1
                autofits += r["has_norm_autofit"]
            if r["x"] is not None and r["w"] and r["w"] < 0.9 * IN(prs.slide_width):
                xs.append(r["x"])
                tops[len(slides)].append(r["y"])
    pitches = []
    for _, ts in tops.items():
        u = sorted(set(round(t, 2) for t in ts))
        pitches += [round(b - a, 3) for a, b in zip(u, u[1:]) if 0.3 < b - a < 4]
    return {
        "slide_w_in": IN(prs.slide_width), "slide_h_in": IN(prs.slide_height),
        "n_slides": len(slides),
        "font_sizes_pt": dict(sorted(size_ctr.items(), key=lambda t: -t[1])),
        "fonts": dict(fonts),
        "column_x_clusters_in": cluster(xs)[:8],
        "row_pitch_clusters_in": cluster(pitches)[:6],
        "anchors": dict(anchors),
        "frames_with_normAutofit": autofits,
        "px_smell": px_smell(size_ctr.keys(), IN(prs.slide_width)),
        "slides": slides,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pptx")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--slides", action="store_true", help="dump per-shape rows too")
    a = ap.parse_args()
    p = profile(a.pptx)
    if not a.slides:
        slides = p.pop("slides")
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=1))
        return
    print(f"{a.pptx}: {p['n_slides']} slides, {p['slide_w_in']}x{p['slide_h_in']} in")
    print(f"font sizes (pt: runs): {p['font_sizes_pt']}")
    print(f"fonts: {p['fonts']}")
    print(f"column x clusters (in: shapes): {p['column_x_clusters_in']}")
    print(f"row pitch clusters (in: gaps):  {p['row_pitch_clusters_in']}")
    print(f"anchors: {p['anchors']}   normAutofit frames: {p['frames_with_normAutofit']}")
    for s in p["px_smell"]:
        print(f"px-smell: {s}")
    if a.slides:
        for i, rows in enumerate(p["slides"], 1):
            print(f"--- slide {i}")
            for r in rows:
                print(" ", json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()

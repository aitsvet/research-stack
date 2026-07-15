#!/usr/bin/env python3
"""Real text measurement for deck layout — no guessed extents, ever.

Resolves a pptx font name to an installed TTF via fontconfig (metric fallback:
Arial -> Liberation Sans etc.), measures with Pillow, wraps and balances lines.
Importable module + CLI:

  measure.py width  "text" --font Arial --pt 25
  measure.py fit    "text" --font Arial --pt 25 --box-w 5.0 --box-h 1.2 [--spacing 1.15]
  measure.py wrap   "text" --font Arial --pt 25 --box-w 5.0 [--balance]
"""
import argparse
import functools
import json
import subprocess
import sys

from PIL import ImageFont

EMU_PER_IN = 914400
PX_PER_IN = 96.0  # CSS reference pixel

# Metric-compatible substitutions when the named face is not installed.
FALLBACK = {
    "arial": "Liberation Sans",
    "helvetica": "Liberation Sans",
    "times new roman": "Liberation Serif",
    "courier new": "Liberation Mono",
    "calibri": "Carlito",
    "cambria": "Caladea",
}


@functools.lru_cache(maxsize=64)
def resolve_font(name, bold=False):
    """Return path to a TTF for `name`, honouring metric-compatible fallbacks."""
    query = FALLBACK.get(name.lower(), name)
    if bold:
        query += ":bold"
    out = subprocess.run(
        ["fc-match", "-f", "%{file}", query], capture_output=True, text=True
    ).stdout.strip()
    if not out:
        raise RuntimeError(f"fontconfig found nothing for {name!r}")
    return out


@functools.lru_cache(maxsize=256)
def _font(name, pt, bold):
    # Pillow takes pixel sizes; at 96dpi 1pt = 4/3 px. Measure at 4x for precision.
    return ImageFont.truetype(resolve_font(name, bold), int(round(pt * 4 / 3 * 4)))


def text_width_in(text, name, pt, bold=False):
    """Rendered width of a single line, in inches."""
    return _font(name, pt, bold).getlength(text) / 4 / PX_PER_IN


def line_height_in(pt, spacing=1.2):
    """Line box height in inches for a given point size and line-spacing factor."""
    return pt * spacing / 72.0


def wrap(text, name, pt, box_w_in, bold=False):
    """Greedy word wrap against measured widths. Returns list of lines."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and text_width_in(cand, name, pt, bold) > box_w_in:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def balance(text, name, pt, box_w_in, bold=False):
    """Wrap, then rebalance to minimise raggedness at the same line count."""
    lines = wrap(text, name, pt, box_w_in, bold)
    n = len(lines)
    if n < 2:
        return lines
    words = text.split()
    best, best_score = lines, _rag(lines, name, pt, bold)
    # Try every composition of words into n lines that fits; n and words are
    # small on slides, greedy neighbourhood search is enough.
    for _ in range(20):
        improved = False
        for i in range(n - 1):
            for move in (+1, -1):
                cand = _shift(best, i, move)
                if cand is None:
                    continue
                if any(text_width_in(l, name, pt, bold) > box_w_in for l in cand):
                    continue
                s = _rag(cand, name, pt, bold)
                if s < best_score:
                    best, best_score, improved = cand, s, True
        if not improved:
            break
    assert " ".join(" ".join(l.split()) for l in best).split() == words
    return best


def _shift(lines, i, direction):
    a, b = lines[i].split(), lines[i + 1].split()
    if direction > 0 and len(a) > 1:
        b.insert(0, a.pop())
    elif direction < 0 and len(b) > 1:
        a.append(b.pop(0))
    else:
        return None
    out = list(lines)
    out[i], out[i + 1] = " ".join(a), " ".join(b)
    return out


def _rag(lines, name, pt, bold):
    ws = [text_width_in(l, name, pt, bold) for l in lines]
    return max(ws) - min(ws)


def fits(text, name, pt, box_w_in, box_h_in, spacing=1.2, bold=False):
    """(fits, lines, needed_h_in) for text in a box of given inner size."""
    lines = wrap(text, name, pt, box_w_in, bold)
    needed = len(lines) * line_height_in(pt, spacing)
    return needed <= box_h_in + 1e-3, lines, needed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["width", "fit", "wrap"])
    ap.add_argument("text")
    ap.add_argument("--font", default="Arial")
    ap.add_argument("--pt", type=float, required=True)
    ap.add_argument("--bold", action="store_true")
    ap.add_argument("--box-w", type=float, help="inner box width, inches")
    ap.add_argument("--box-h", type=float, help="inner box height, inches")
    ap.add_argument("--spacing", type=float, default=1.2)
    ap.add_argument("--balance", action="store_true")
    a = ap.parse_args()
    if a.mode == "width":
        print(f"{text_width_in(a.text, a.font, a.pt, a.bold):.3f}")
    elif a.mode == "wrap":
        fn = balance if a.balance else wrap
        print(json.dumps(fn(a.text, a.font, a.pt, a.box_w, a.bold), ensure_ascii=False, indent=1))
    else:
        ok, lines, needed = fits(a.text, a.font, a.pt, a.box_w, a.box_h, a.spacing, a.bold)
        print(json.dumps({"fits": ok, "lines": lines, "needed_h_in": round(needed, 3)},
                         ensure_ascii=False, indent=1))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

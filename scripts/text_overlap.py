#!/usr/bin/env python3
"""Which parts of a text corpus repeat each other, and where.

    text_overlap.py <file-or-dir>... [--ext md,txt] [--exclude GLOB ...]
        [--k 8] [--min-cover 0.05] [--min-run 60] [--max-runs 5]
        [--out report.md] [--json report.json]

Every file is folded to word tokens (`textfold.words`) and cut into
overlapping k-word shingles. For each pair of files the report gives the
coverage of each side — the share of its distinct shingles that also occur in
the other — and, for pairs above `--min-cover`, the longest shared runs with
their location on both sides: the `{N}` page of paginated Markdown
(`pagemd`), otherwise the line number, plus the opening words of the run.

Coverage counts shared wording, not shared meaning: common boilerplate
(a bibliography, a law title repeated in headers) raises it, a paraphrase or
a translation does not show at all. A run is a stretch of the first file whose
shingles all occur somewhere in the second, with gaps shorter than k tolerated;
its location in the second file is where its first shingle occurs there.
"""
import argparse
import fnmatch
import json
import os
import re
import sys
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pagemd import MARK  # noqa: E402
from textfold import words  # noqa: E402


def collect(paths, exts, excludes):
    out = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if f.rsplit(".", 1)[-1].lower() in exts:
                    out.append(os.path.join(root, f))
    rel = lambda f: os.path.relpath(f)
    return [f for f in out if not any(fnmatch.fnmatch(rel(f), g) for g in excludes)]


def tokenize(path):
    """Word tokens and, per token, its location label (page or line)."""
    toks, locs = [], []
    paged = False
    page = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln, line in enumerate(fh, 1):
            m = MARK.match(line)
            if m:
                paged, page = True, int(m.group(1))
                continue
            w = words(line)
            toks.extend(w)
            locs.extend([("p", page) if paged else ("l", ln)] * len(w))
    return toks, locs


def label(loc):
    kind, n = loc
    return ("p.{%s}" % n) if kind == "p" else ("line %s" % n)


def shingles(toks, k):
    return [hash(" ".join(toks[i:i + k])) for i in range(max(0, len(toks) - k + 1))]


def runs(sh_a, set_b, k, min_run):
    """Maximal stretches of A whose shingles occur in B: (start, end) word index."""
    out, start, last = [], None, None
    for i, h in enumerate(sh_a):
        if h in set_b:
            if start is None:
                start = i
            elif i - last > k:
                out.append((start, last + k))
                start = i
            last = i
    if start is not None:
        out.append((start, last + k))
    return [r for r in out if r[1] - r[0] >= min_run]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--ext", default="md,txt")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--min-cover", type=float, default=0.05)
    ap.add_argument("--min-run", type=int, default=60)
    ap.add_argument("--max-runs", type=int, default=5)
    ap.add_argument("--out")
    ap.add_argument("--json")
    a = ap.parse_args()

    files = collect(a.paths, set(a.ext.split(",")), a.exclude)
    docs = {}
    for f in files:
        toks, locs = tokenize(f)
        sh = shingles(toks, a.k)
        if sh:
            first = {}
            for i, h in enumerate(sh):
                first.setdefault(h, i)
            docs[f] = {"toks": toks, "locs": locs, "sh": sh, "set": set(sh), "first": first}
    print("files: %d with text, %d skipped as empty" % (len(docs), len(files) - len(docs)),
          file=sys.stderr)

    pairs = []
    for fa, fb in combinations(sorted(docs), 2):
        A, B = docs[fa], docs[fb]
        inter = len(A["set"] & B["set"])
        if not inter:
            continue
        ca, cb = inter / len(A["set"]), inter / len(B["set"])
        if max(ca, cb) < a.min_cover:
            continue
        # describe runs from the side that is more covered (the "contained" one)
        src, dst = (fa, fb) if ca >= cb else (fb, fa)
        S, D = docs[src], docs[dst]
        rr = sorted(runs(S["sh"], D["set"], a.k, a.min_run), key=lambda r: r[0] - r[1])
        regions = []
        for s, e in rr[:a.max_runs]:
            j = D["first"][S["sh"][s]]
            regions.append({
                "words": e - s,
                "in": src, "at": label(S["locs"][s]), "to": label(S["locs"][min(e, len(S["locs"])) - 1]),
                "other": dst, "other_at": label(D["locs"][j]),
                "opening": " ".join(S["toks"][s:s + 12]),
            })
        pairs.append({"a": fa, "b": fb, "cover_a": ca, "cover_b": cb, "shared": inter,
                      "runs_total": len(rr), "regions": regions})
    pairs.sort(key=lambda p: -max(p["cover_a"], p["cover_b"]))

    lines = ["| A | B | cover A | cover B | shared runs ≥%d words |" % a.min_run,
             "|---|---|---:|---:|---:|"]
    for p in pairs:
        lines.append("| `%s` | `%s` | %.1f%% | %.1f%% | %d |" % (
            p["a"], p["b"], 100 * p["cover_a"], 100 * p["cover_b"], p["runs_total"]))
    lines.append("")
    for p in pairs:
        if not p["regions"]:
            continue
        lines.append("### `%s` ↔ `%s`" % (p["a"], p["b"]))
        lines.append("")
        for r in p["regions"]:
            lines.append("- %d words: `%s` %s–%s = `%s` %s — \"%s…\"" % (
                r["words"], r["in"], r["at"], r["to"], r["other"], r["other_at"], r["opening"]))
        lines.append("")
    text = "\n".join(lines)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"k": a.k, "min_cover": a.min_cover, "min_run": a.min_run,
                       "files": sorted(docs), "pairs": pairs}, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

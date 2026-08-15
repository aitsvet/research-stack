#!/usr/bin/env python3
"""Check a paper's internal numeric-citation integrity.

Renumbering a bibliography by hand is where these drafts break: a reference is
dropped, everything after it shifts, and the body keeps pointing at the old
numbers. The damage is invisible on reading — `[17]` still looks like a
citation — so it needs a mechanical check.

Reports:
  DANGLING   body cites [n] with no entry n in the bibliography
  UNCITED    bibliography has entry n that the body never cites
  GAP        bibliography numbering is not 1..N contiguous
  DUPLICATE  the same number appears twice in the bibliography

Body citations are pure-numeric brackets only: [5], [17; 18], [15-28], [17-19; 21]
(hyphen or en/em dash). Prose brackets ([ст. 429.2], [Рис. 2], [Электронный
ресурс]) are ignored, and so is everything from the bibliography heading onward.

Usage:
  check_citations.py <paper.md> [<paper.md> ...] [--heading REGEX]
"""
import argparse
import re
import sys

DEFAULT_HEADING = r"^#+\s*(Библиографический список|Список литературы|References)"
DASH = "‐-―-"                      # hyphen plus the dash block
ATOM = r"\d+(?:\s*[%s]\s*\d+)?" % DASH       # "12" or "15–28"
CITE = re.compile(r"\[(%s(?:\s*;\s*%s)*)\]" % (ATOM, ATOM))
RANGE = re.compile(r"(\d+)\s*[%s]\s*(\d+)" % DASH)
ENTRY = re.compile(r"^(\d+)\.\s+\S")


def expand(group):
    """[15–28; 21] -> {15..28, 21}. A range is inclusive of both ends."""
    out = set()
    for part in re.split(r"\s*;\s*", group):
        m = RANGE.fullmatch(part.strip())
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            out.update(range(min(lo, hi), max(lo, hi) + 1))
        else:
            out.add(int(part))
    return out


def check(path, heading_re):
    text = open(path, encoding="utf-8").read()
    m = re.search(heading_re, text, re.M)
    if not m:
        return ["%s: no bibliography heading found" % path]
    body, biblio = text[:m.start()], text[m.end():]

    cited = set()
    for grp in CITE.findall(body):
        cited.update(expand(grp))

    listed, dupes = [], []
    for line in biblio.splitlines():
        e = ENTRY.match(line.strip())
        if e:
            n = int(e.group(1))
            (dupes if n in listed else listed).append(n)

    problems = []
    for n in sorted(dupes):
        problems.append("DUPLICATE entry %d" % n)
    for n in sorted(cited - set(listed)):
        problems.append("DANGLING  [%d] cited, not in bibliography" % n)
    for n in sorted(set(listed) - cited):
        problems.append("UNCITED   entry %d never cited in body" % n)
    if listed and sorted(set(listed)) != list(range(1, max(listed) + 1)):
        missing = sorted(set(range(1, max(listed) + 1)) - set(listed))
        problems.append("GAP       bibliography skips %s"
                        % ", ".join(str(x) for x in missing))

    print("%s: %d entries, %d distinct citations — %s"
          % (path, len(listed), len(cited),
             "OK" if not problems else "%d problems" % len(problems)))
    return ["  " + p for p in problems]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("papers", nargs="+")
    ap.add_argument("--heading", default=DEFAULT_HEADING)
    a = ap.parse_args()
    bad = 0
    for p in a.papers:
        for line in check(p, a.heading):
            bad += 1
            print(line)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

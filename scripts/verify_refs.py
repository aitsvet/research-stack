#!/usr/bin/env python3
"""Verify the bibliography of a draft paper against Crossref.

Scans the input file for DOI tokens, looks each up in Crossref, and compares
the citation's surrounding text against the canonical metadata. Reports four
classes of finding:

  OK         — author surnames and title overlap above thresholds
  AUTHORS    — DOI resolves but cited surnames don't appear in Crossref author list
  TITLE      — DOI resolves but cited title and Crossref title share < 30% tokens
  VENUE      — DOI resolves but cited venue and Crossref container-title differ
  NOTFOUND   — Crossref returns 404 (DOI is invalid)
  ERROR      — network or parsing failure

Motivating failure mode: a draft contains a citation of the form
`Author A.* … DOI 10.XXXX/...` where the DOI resolves to a real paper whose
authors are NOT "Author A." but the citation still looks plausible. A pure
"does this DOI exist?" check cannot catch these; surname comparison can.

Usage:
  verify_refs.py <markdown_or_tex_file> [--json] [--max-context 600] [--per-line]

  --per-line   treat each line as one citation. Use for a formatted numbered
               bibliography (one reference per line, each ending in its own DOI):
               the default char-window bleeds across neighbouring reference lines
               and produces spurious TITLE/VENUE flags. Author matching is
               diacritic-folded so accented surnames (Bölte, Cortés) still match.

The script reads stdin if the path is `-`.

Output: a human-readable table by default; `--json` emits a list of finding
dicts suitable for downstream tooling.

Env:
  OPENALEX_EMAIL    polite-pool identifier for the Crossref User-Agent header
                    (Crossref doesn't require it but it raises the rate limit).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

EMAIL = os.environ.get("OPENALEX_EMAIL") or os.environ.get("ZOTERO_USER_EMAIL", "")
UA = f"research-stack/1.0 (mailto:{EMAIL})" if EMAIL else "research-stack/1.0"

# DOI pattern from https://www.crossref.org/blog/dois-and-matching-regular-expressions/
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>{}()\[\]]+", re.IGNORECASE)
# Trailing punctuation that often glues to a DOI in prose
TRAILING_PUNCT = ".,;:)]>"

# Author surname extraction: tokens of at least 3 letters, capital-first or all-caps,
# but not the common citation-stop words.
STOPWORDS = set("""
the and of for with on in to a an by from as is at that this be or which but not
edition vol volume no pages page doi url isbn issn pp p international journal
review research science studies report based using approach analysis framework
study trends bibliometric literature systematic preprint
""".split())


def fetch_crossref(doi: str, max_attempts: int = 3, delay: float = 1.0):
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/.()')}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last_err = None
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)["message"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # not-found
            last_err = e
        except Exception as e:
            last_err = e
        time.sleep(delay)
        delay *= 2
    raise last_err if last_err else RuntimeError("crossref unreachable")


def fold(s: str) -> str:
    """Lowercase and strip diacritics so accented names/titles match (Bölte -> bolte)."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def tokenize(s: str) -> set[str]:
    """Diacritic-folded word tokens of length >= 3, minus stopwords."""
    return {
        t for t in re.findall(r"[a-zа-я0-9]+", fold(s))
        if len(t) >= 3 and t not in STOPWORDS
    }


def cited_surnames(context: str) -> set[str]:
    """Extract capitalized name-like tokens from the citation context, diacritic-folded
    so accented surnames match (handles 'Bölte', 'Cortés-Albornoz')."""
    out: set[str] = set()
    for m in re.finditer(r"\b([^\W\d_][\w'\-]+)\b", context, re.UNICODE):
        tok = m.group(1)
        if not tok[:1].isupper():
            continue
        f = fold(tok)
        # >= 2 so short CJK-origin surnames (Na, Xu, Li) are captured; STOPWORDS
        # drops 2-letter function words (In, Of, To...). Unmatched noise tokens are
        # harmless — the check passes if ANY cited surname matches Crossref.
        if len(f) >= 2 and f not in STOPWORDS:
            out.add(f)
    return out


def cited_title_and_venue(context: str) -> tuple[str, str]:
    """Heuristic: split on ' // ' (Russian/ISO 690 style); first half is title-ish,
    second half venue-ish. For // -less citations, returns the full context as title."""
    if " // " in context:
        title, after = context.split(" // ", 1)
    else:
        title, after = context, ""
    return title.strip(" .,;"), after.strip(" .,;")


def crossref_authors(msg: dict) -> list[str]:
    out = []
    for a in (msg.get("author") or []):
        ln = (a.get("family") or "").strip()
        if ln:
            out.append(fold(ln))
    return out


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def grab_context(text: str, doi_start: int, doi_end: int, window: int) -> str:
    """Pull a window around the DOI as the citation context."""
    a = max(0, doi_start - window)
    b = min(len(text), doi_end + 64)
    return text[a:b]


def evaluate_citation(doi: str, ctx: str, args) -> dict:
    """Look one DOI up in Crossref and compare it against its citation context."""
    cite_surnames = cited_surnames(ctx)
    cite_title, cite_venue = cited_title_and_venue(ctx)
    cite_title_tokens = tokenize(cite_title)
    cite_venue_tokens = tokenize(cite_venue)
    finding: dict = {"doi": doi, "context": ctx.strip()[:300]}

    try:
        msg = fetch_crossref(doi)
    except Exception as e:
        finding["status"] = "ERROR"
        finding["note"] = str(e)[:120]
        return finding
    if msg is None:
        finding["status"] = "NOTFOUND"
        return finding

    cr_authors = crossref_authors(msg)
    cr_title = re.sub(r"<[^>]+>", "", (msg.get("title") or [""])[0])
    cr_venue = (msg.get("container-title") or [""])[0]
    finding["crossref"] = {"title": cr_title[:120], "authors": cr_authors[:6], "venue": cr_venue[:80]}

    problems = []
    if cite_surnames and cr_authors:
        # Lenient: a cited surname counts as present if it appears anywhere in the
        # (folded) Crossref family names — tolerates multi-word/compound surnames.
        blob = " ".join(cr_authors)
        if not any(s in blob for s in cite_surnames):
            problems.append("AUTHORS")
            finding["author_examples_cited"] = sorted(cite_surnames)[:6]
            finding["author_examples_crossref"] = cr_authors[:6]
    if cite_title_tokens and tokenize(cr_title):
        j = jaccard(cite_title_tokens, tokenize(cr_title))
        finding["title_overlap"] = round(j, 2)
        if j < args.title_threshold:
            problems.append("TITLE")
    if cite_venue_tokens and tokenize(cr_venue):
        j = jaccard(cite_venue_tokens, tokenize(cr_venue))
        finding["venue_overlap"] = round(j, 2)
        if j < args.venue_threshold:
            problems.append("VENUE")
    finding["status"] = "OK" if not problems else "/".join(problems)
    return finding


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", help="Markdown / LaTeX / plain-text file with DOIs (use '-' for stdin)")
    p.add_argument("--json", action="store_true", help="emit findings as JSON")
    p.add_argument("--max-context", type=int, default=600,
                   help="bytes of context before each DOI to treat as citation (default 600)")
    p.add_argument("--per-line", action="store_true",
                   help="treat each line as one citation (for formatted numbered bibliographies, "
                        "where a char-window would bleed across neighbouring reference lines)")
    p.add_argument("--title-threshold", type=float, default=0.30,
                   help="minimum Jaccard overlap of title tokens (default 0.30)")
    p.add_argument("--venue-threshold", type=float, default=0.40,
                   help="minimum Jaccard overlap of venue tokens (default 0.40)")
    args = p.parse_args()

    text = sys.stdin.read() if args.path == "-" else open(args.path, encoding="utf-8").read()

    # Collect (doi, context) pairs, deduped by DOI (first occurrence wins).
    # Default: a char-window before each DOI (good for DOIs embedded in prose).
    # --per-line: the whole line minus the DOI (good for formatted bibliographies,
    # where every line ends in its own DOI and a window would bleed into neighbours).
    findings = []
    items: dict[str, str] = {}
    if args.per_line:
        for line in text.splitlines():
            ctx = DOI_RE.sub(" ", line)  # strip DOI(s) so they don't pollute title/venue tokens
            for m in DOI_RE.finditer(line):
                doi = m.group(0).rstrip(TRAILING_PUNCT)
                items.setdefault(doi.lower(), ctx)
    else:
        for m in DOI_RE.finditer(text):
            doi = m.group(0).rstrip(TRAILING_PUNCT)
            if doi.lower() not in items:
                items[doi.lower()] = grab_context(text, m.start(), m.end(), args.max_context)

    for doi_lc, ctx in items.items():
        findings.append(evaluate_citation(doi_lc, ctx, args))
        time.sleep(0.1)  # polite to Crossref

    if args.json:
        json.dump(findings, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return

    # human-readable
    print(f"{'STATUS':<14} DOI                                       NOTES")
    print(f"{'-'*14} {'-'*40} {'-'*40}")
    counts: dict[str, int] = {}
    for f in findings:
        st = f["status"]
        counts[st] = counts.get(st, 0) + 1
        note = ""
        if st == "NOTFOUND":
            note = "Crossref 404 — DOI does not resolve"
        elif st == "ERROR":
            note = f["note"]
        elif st != "OK":
            cr = f.get("crossref", {})
            bits = []
            if "AUTHORS" in st:
                bits.append(f"crossref authors: {', '.join(cr.get('authors', [])[:3])}")
            if "TITLE" in st:
                bits.append(f"crossref title: {cr.get('title','')[:55]}")
            if "VENUE" in st:
                bits.append(f"crossref venue: {cr.get('venue','')}")
            note = "; ".join(bits)
        print(f"{st:<14} {f['doi'][:40]:<40} {note}")
    print()
    print("Summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if any(s != "OK" for s in (f["status"] for f in findings)):
        sys.exit(1)


if __name__ == "__main__":
    main()

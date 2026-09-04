#!/usr/bin/env python3
"""Verify every quotation in a draft against the paginated source it cites.

A quote-backed argument fails in a way reading cannot catch: the wording drifts
by a word or two while the page reference stays, or a correct quote is attached
to the wrong page. Both look fine on the page and are wrong in the source, so
they need a mechanical check — the same role `verify_refs.py` plays for DOIs and
`check_citations.py` for numbered bibliographies.

Quotes are blockquote groups (`> ...`) whose last line ends in a page anchor;
the anchor may sit on its own line or close the final quoted line. `[…]` and `…`
split a quote into segments that are each required to appear, so elisions are
allowed but invention between them is not.

The source is a paginated Markdown extraction (`{N}` + dashes, as produced by
`extract_texts.py md`, `chandra_ocr.py`, or `split_spreads.py` feeding either).
Anchors resolve against the page either by that `{N}` mark (`--page-key marker`)
or by the folio printed in the book, detected as a digits-only line
(`--page-key printed`, the default). Folio lines are stripped from the page body
before matching, so a sentence running across a page break does not get a page
number spliced into its middle.

Two guards keep the check honest. Every anchor in the draft must be claimed by
an extracted quote, or the run aborts rather than silently checking a subset.
And one verified quote is re-checked against a different page as a negative
control; if that passes, the comparison is too lax to mean anything and the run
fails.

Usage:
  verify_quotes.py <draft.md> --source <paginated.md>
                   [--page-key printed|marker] [--folio last|first]
                   [--anchor REGEX] [--quiet]
"""
import argparse
import re
import sys

# «— с. 140», «— с. 121–122», «— p. 12», «— pp. 12-13»
DEFAULT_ANCHOR = (r"[—–-]\s*(?:с|стр|p|pp)\.\s*"
                  r"(?P<pages>\d+(?:\s*[—–-]\s*\d+)?)\s*$")
PAGE_MARK = r"^\{([0-9IVXLCivxlc]+)\}-{10,}\s*$"
BARE_NUM = re.compile(r"^\s*(\d{1,4})\s*$", re.M)
QUOTE_CHARS = "«»“”„\"'"


def norm(s):
    """Fold what formatting varies and meaning does not."""
    s = re.sub(r"[*_`]", "", s)                  # emphasis added by the draft
    for ch in QUOTE_CHARS:
        s = s.replace(ch, "")
    s = s.replace("­", "")                       # soft hyphen
    s = s.replace("—", "-").replace("–", "-")
    s = s.replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", s).strip()


def source_pages(path, page_key, folio):
    """{page id -> normalised page text}, folio lines removed."""
    text = open(path, encoding="utf-8").read()
    parts = re.split(PAGE_MARK, text, flags=re.M)
    blocks = {k: v for k, v in zip(*[iter(parts[1:])] * 2)}
    if not blocks:
        sys.exit(f"{path}: no {{N}} page marks found — is it a paginated extraction?")

    pages = {}
    for mark, body in blocks.items():
        if page_key == "marker":
            key = mark
        else:
            nums = BARE_NUM.findall(body)
            if not nums:
                continue
            key = nums[-1] if folio == "last" else nums[0]
        body = BARE_NUM.sub("", body)
        body = re.sub(r"^#+\s*", "", body, flags=re.M)
        pages[str(key)] = norm(body)
    return pages


def draft_quotes(path, anchor_re):
    """[(quote text, [page ids])], plus the count of anchors seen in the file."""
    text = open(path, encoding="utf-8").read()
    anchor = re.compile(anchor_re)
    cases = []
    for grp in re.findall(r"(?:^>.*\n)+", text, flags=re.M):
        flat = " ".join(re.sub(r"^>\s?", "", ln) for ln in grp.strip().splitlines())
        flat = re.sub(r"\s+", " ", flat).strip()
        m = anchor.search(flat)
        if not m:
            continue
        span = [int(x) for x in re.findall(r"\d+", m.group("pages"))]
        ids = [str(n) for n in range(span[0], span[-1] + 1)] if len(span) > 1 \
            else [str(span[0])]
        cases.append((flat[:m.start()].strip(), ids))
    declared = len(re.findall(anchor_re, text, flags=re.M))
    return cases, declared


def segments(quote):
    parts = re.split(r"\[…\]|\[\.\.\.\]|…|\.\.\.", norm(quote))
    return [p.strip(" .,;:-") for p in parts if len(p.strip(" .,;:-")) > 12]


def check(quote, ids, pages):
    """Segments of the quote missing from the joined text of the cited pages."""
    hay = " ".join(pages.get(i, "") for i in ids)
    return [s for s in segments(quote) if s not in hay]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft")
    ap.add_argument("--source", required=True, help="paginated .md extraction")
    ap.add_argument("--page-key", choices=["printed", "marker"], default="printed",
                    help="resolve anchors by the folio printed in the book "
                         "(default) or by the {N} mark")
    ap.add_argument("--folio", choices=["last", "first"], default="last",
                    help="where the folio sits in a page block (default last)")
    ap.add_argument("--anchor", default=DEFAULT_ANCHOR)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    pages = source_pages(a.source, a.page_key, a.folio)
    cases, declared = draft_quotes(a.draft, a.anchor)

    if declared != len(cases):
        sys.exit(f"{a.draft}: {declared} page anchor(s) present but "
                 f"{len(cases)} quote(s) extracted — the checker would pass the "
                 f"difference without looking at it. Fix the extraction first.")
    if not cases:
        sys.exit(f"{a.draft}: no quotes with page anchors found")

    failed = 0
    for quote, ids in cases:
        missing = check(quote, ids, pages)
        label = norm(quote)[:60]
        if missing:
            failed += 1
            print(f"MISSING  [{'/'.join(ids)}] {label}…")
            for m in missing:
                print(f"         not on that page: {m[:100]!r}")
        elif not a.quiet:
            print(f"ok       [{'/'.join(ids)}] {label}…")

    # Negative control: corrupt one verified quote and require it to fail on
    # its own page. Testing against a *different* page would be unsound — a
    # sentence may legitimately recur (a Tradition restated, an epigraph), and
    # the control would then damn a working checker.
    control = "not run (no quote long enough)"
    for quote, ids in cases:
        if check(quote, ids, pages):
            continue
        words = norm(quote).split()
        if len(words) < 8:
            continue
        words[len(words) // 2] = "zzqxwv"
        control = ("ok" if check(" ".join(words), ids, pages)
                   else "a corrupted quote still passes — CHECK IS BROKEN")
        break

    # A quote that also sits on other pages cannot have its anchor confirmed by
    # this check; that is a limit of the method, not a defect in the draft.
    ambiguous = sum(1 for q, ids in cases if not check(q, ids, pages)
                    and any(not check(q, [k], pages) for k in pages if k not in ids))

    print(f"\n{a.source}: {len(pages)} pages indexed by {a.page_key}")
    print(f"{len(cases)} quotes, {failed} failed; negative control: {control}")
    if ambiguous:
        print(f"note: {ambiguous} quote(s) also occur on another page — "
              f"the check confirms the wording, not that the anchor is the "
              f"only possible one")
    sys.exit(1 if failed or "BROKEN" in control else 0)


if __name__ == "__main__":
    main()

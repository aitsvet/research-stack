#!/usr/bin/env python3
"""Reconcile a marker-produced .md against a clean pymupdf4llm text-layer
reference (.txt from `extract_texts.py md`), fixing marker's OCR errors while
preserving the marker file's structure (tables, image refs, page rules).

Marker (surya OCR on GPU) introduces:
  - Latin<->Cyrillic homoglyph corruptions  (a Latin acronym rendered in Cyrillic
    look-alikes, e.g. ABC->АВС; or a Cyrillic word rendered in Latin look-alikes)
  - dropped / merged words
  - over-aggressive headings  (plain line -> "## ...")

The reference reads the embedded PDF text layer, so it has the correct glyphs
and word stream. We align the two word streams (difflib) and overlay the
reference's words onto the marker file ONLY where the change is a safe, local
textual correction. Markup tokens (#, *, |, image refs, {N}----- rules) are
never aligned, so structure is preserved. Headings the reference treats as
plain prose are demoted.

Usage:
  patch_marker.py <marker.md> <reference.txt> [--out FILE] [--inplace]
                  [--report] [--no-headings] [--max-replace N] [--max-insert N]
"""
from __future__ import annotations
import argparse, difflib, re, sys, unicodedata

# --- Cyrillic<->Latin homoglyph folding (for confidence scoring only) ----------
_HOMO = {
    "А":"A","В":"B","Е":"E","К":"K","М":"M","Н":"H","О":"O","Р":"P","С":"C",
    "Т":"T","У":"Y","Х":"X","а":"a","е":"e","о":"o","р":"p","с":"c","у":"y",
    "х":"x","к":"k","м":"m","т":"t","в":"b","н":"h","Ѕ":"S","і":"i","І":"I",
    "ј":"j","Ј":"J","Ո":"n",
}
def _fold(s: str) -> str:
    return "".join(_HOMO.get(ch, ch) for ch in s)

def _has_lat(s: str) -> bool:
    return any("a" <= c.lower() <= "z" for c in s)
def _has_cyr(s: str) -> bool:
    return any("Ѐ" <= c <= "ԯ" for c in s)

# Word = run of letters/digits across Latin+Cyrillic ONLY. Deliberately excludes
# '_' and other markup so reference italics (_x_) / bold (**x**) don't pollute
# the word stream and so token boundaries are stable.
WORD_RE = re.compile(r"[0-9A-Za-zЀ-ӿёЁ]+")

def tokenize(text: str):
    """Split into a flat token list, tagging each token word/other. Returns
    (tokens, word_indices) where tokens are raw substrings whose concatenation
    == text, and word_indices lists positions of \\w+ tokens."""
    toks, widx = [], []
    pos = 0
    for m in WORD_RE.finditer(text):
        if m.start() > pos:
            toks.append(text[pos:m.start()])
        widx.append(len(toks))
        toks.append(m.group(0))
        pos = m.end()
    if pos < len(text):
        toks.append(text[pos:])
    return toks, widx

def clean_ref_word(w: str) -> str:
    """Normalize a reference word: NFC + strip stray combining marks
    (pymupdf4llm sometimes leaves a combining breve, e.g. 'внешней̆')."""
    w = unicodedata.normalize("NFC", w)
    return "".join(ch for ch in w if not unicodedata.combining(ch))

def char_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()

def _safe_fix(am: str, bm: str, ratio: float) -> bool:
    """Is replacing marker word `am` with reference word `bm` a safe correction?"""
    if am == bm:
        return False
    if len(am) <= 1 or len(bm) <= 1:
        return False            # single chars (list markers, initials) too risky
    if am.lower() == bm.lower():
        return False            # case-only difference is not a corruption fix
    if _fold(am) == _fold(bm):
        # Homoglyph swap (same letters, different scripts). Decide direction:
        #  - MIXED-script marker token ('caйт','СВDС','Таһаwwut') -> always
        #    corrupt, trust the reference.
        #  - pure-Cyrillic -> pure-Latin: OCR Cyrillicized a Latin acronym
        #    (pure-Cyrillic acronym -> its Latin form, e.g. АВС->ABC) -> apply.
        #  - pure-Latin -> pure-Cyrillic: OCR Latinized a Cyrillic word
        #    (pecypc->ресурс) -> apply, UNLESS it's a Roman numeral the PDF
        #    encoded in Cyrillic (XXXVI) -> keep the Latin marker token.
        if _has_lat(am) and _has_cyr(am):
            return True
        a_lat, a_cyr = _has_lat(am) and not _has_cyr(am), _has_cyr(am) and not _has_lat(am)
        b_lat, b_cyr = _has_lat(bm) and not _has_cyr(bm), _has_cyr(bm) and not _has_lat(bm)
        if a_cyr and b_lat:
            return True
        if a_lat and b_cyr:
            return not re.match(r"^[IVXLCDM]+$", am, re.I)
        return False
    fa, fb = _fold(am), _fold(bm)
    # Reference-side glyph loss: pymupdf4llm drops fi/fl ligatures, so the ref
    # word is the marker word minus chars (finance->nance, flows->ows). Keep
    # marker. (The opposite — marker dropped chars, ref is longer, e.g.
    # нако->Однако — is NOT caught here, so it still gets fixed.)
    if len(fb) < len(fa) and fb in fa:
        return False
    # Below here = fuzzy (non-homoglyph) OCR-misread matching. Don't apply it to
    # numbers / IDs / hashes: ref merges adjacent digits (12->121, 0037720->О...)
    # and hashes can't be verified by similarity. But DO allow a word that merely
    # contains a stray digit-homoglyph (balance0f->balanceOf). So skip only when
    # the pair is digit-majority or a long alphanumeric ID.
    nd = sum(c.isdigit() for c in am + bm)
    nl = sum(c.isalpha() for c in am + bm)
    if nd and (nd >= nl or max(len(am), len(bm)) >= 12):
        return False
    # OCR misread (e.g. a garbled Cyrillic word 'слелки'->'сделки'): require the
    # two to be quite similar AND same rough length so we don't swap real words.
    # Compare folded too, so partial-homoglyph misreads (п->n, г->r) score high.
    if abs(len(am) - len(bm)) <= 2 and \
       max(char_ratio(am, bm), char_ratio(fa, fb)) >= ratio:
        return True
    return False

def reconcile_words(marker_md: str, ref_txt: str, max_replace: int, max_insert: int,
                    log: list, ratio: float = 0.55, do_inserts: bool = False) -> str:
    mtoks, mwidx = tokenize(marker_md)
    mwset = set(mwidx)
    mwords = [mtoks[i] for i in mwidx]
    rwords = [clean_ref_word(w) for w in WORD_RE.findall(ref_txt)]

    sm = difflib.SequenceMatcher(None, mwords, rwords, autojunk=False)
    repl = {}          # marker-word-ordinal -> replacement text (in place, 1:1)
    inserts = {}       # marker-word-ordinal -> ref words to splice in before it

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            a = mwords[i1:i2]; b = rwords[j1:j2]
            # Only equal-length runs: map word-for-word IN PLACE so token
            # boundaries and all surrounding spacing/markup stay intact.
            # (Unequal runs are usually a word split/merge or table text — unsafe.)
            if len(a) != len(b) or len(a) > max_replace:
                log.append(("skip-replace", " ".join(a)[:60], " ".join(b)[:60]))
                continue
            for t in range(len(a)):
                if _safe_fix(a[t], b[t], ratio):
                    repl[i1 + t] = b[t]
                    log.append(("replace", a[t], b[t]))
                else:
                    log.append(("skip-word", a[t], b[t]))
        elif tag == "insert":
            b = rwords[j1:j2]
            # Inserts are noisy: the text-layer reader also picks up running
            # headers/footers/page numbers that marker strips. Off by default.
            if not do_inserts or not (0 < i1 < len(mwords)) or len(b) > max_insert \
               or all(w.isdigit() for w in b):
                log.append(("skip-insert", "", " ".join(b)[:60]))
                continue
            inserts.setdefault(i1, []).extend(b)
            log.append(("insert", "", " ".join(b)[:60]))
        elif tag == "delete":
            # marker has words the reference lacks (often table/figure text the
            # reference ignores). Keep them — deleting is unsafe.
            log.append(("keep-delete", " ".join(mwords[i1:i2])[:60], ""))

    out = []
    word_ord = -1
    for ti, tok in enumerate(mtoks):
        if ti in mwset:
            word_ord += 1
            if word_ord in inserts:
                out.append(" ".join(inserts[word_ord]) + " ")
            out.append(repl.get(word_ord, tok))
        else:
            out.append(tok)
    return "".join(out)

HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

def ref_heading_levels(ref_txt: str):
    """Map normalized heading/prose text -> heading level in reference
    (0 == appears as non-heading prose)."""
    levels = {}
    prose = set()
    for line in ref_txt.splitlines():
        m = HEAD_RE.match(line)
        if m:
            key = _norm_head(m.group(2))
            if key:
                levels[key] = min(levels.get(key, 9), len(m.group(1)))
        else:
            s = line.strip().strip("*_ ")
            if s:
                prose.add(_norm_head(s))
    return levels, prose

def _norm_head(s: str) -> str:
    s = re.sub(r"[*_`#]", "", s).strip().lower()
    return re.sub(r"\s+", " ", s)

def demote_headings(md: str, ref_txt: str, log: list) -> str:
    levels, prose = ref_heading_levels(ref_txt)
    out = []
    for line in md.splitlines(keepends=True):
        nl = "\n" if line.endswith("\n") else ""
        body = line[:-1] if nl else line
        m = HEAD_RE.match(body)
        if not m:
            out.append(line); continue
        key = _norm_head(m.group(2))
        if not key:
            out.append(line); continue
        if key in levels:
            lvl = levels[key]
            if lvl != len(m.group(1)):
                out.append("#" * lvl + " " + m.group(2) + nl)
                log.append(("head-relevel", m.group(0), str(lvl)))
            else:
                out.append(line)
        elif key in prose:
            # reference shows this as plain prose -> demote (drop heading marks)
            out.append(m.group(2) + nl)
            log.append(("head-demote", m.group(0), ""))
        else:
            out.append(line)  # unknown -> leave marker's choice
    return "".join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("marker"); ap.add_argument("reference")
    ap.add_argument("--out", default=None)
    ap.add_argument("--inplace", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--headings", action="store_true",
                    help="also demote/relevel headings to the reference "
                         "(unreliable: the text-layer reader under-detects "
                         "headings, so this can erase real section titles)")
    ap.add_argument("--inserts", action="store_true",
                    help="also splice in words the reference has but marker "
                         "dropped (noisy: may pull in headers/footers)")
    ap.add_argument("--ratio", type=float, default=0.55)
    ap.add_argument("--max-replace", type=int, default=8)
    ap.add_argument("--max-insert", type=int, default=4)
    a = ap.parse_args()

    marker_md = open(a.marker, encoding="utf-8").read()
    ref_txt   = open(a.reference, encoding="utf-8").read()
    log = []
    patched = reconcile_words(marker_md, ref_txt, a.max_replace, a.max_insert,
                              log, a.ratio, a.inserts)
    if a.headings:
        patched = demote_headings(patched, ref_txt, log)

    if a.report:
        from collections import Counter
        c = Counter(k for k, *_ in log)
        sys.stderr.write(f"[{a.marker}] " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())) + "\n")
        for k, av, bv in log:
            if k in ("replace", "insert", "head-demote", "head-relevel"):
                sys.stderr.write(f"    {k}: {av!r} -> {bv!r}\n")

    if a.inplace:
        open(a.marker, "w", encoding="utf-8").write(patched)
    elif a.out:
        open(a.out, "w", encoding="utf-8").write(patched)
    else:
        sys.stdout.write(patched)

if __name__ == "__main__":
    main()

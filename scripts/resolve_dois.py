#!/usr/bin/env python3
"""Resolve DOIs for a wanted-list of papers via Crossref title search.

Never type a DOI from memory: LLM-recalled DOIs are frequently well-formed but
point at a different paper, and the citation then looks verifiable while being
wrong. This asks Crossref for the title and reports the match with enough
evidence (returned title/author/year/venue) to judge it.

Emits `resolved.json` ({id: {doi, title, ...}, ...}) plus a `dois.json` list in
the shape `discover.py --dois` expects. Anything below the score threshold or
with a first-author mismatch is written to `unresolved` for manual handling.

Usage: resolve_dois.py wanted.json --out-dir DIR [--mailto EMAIL]
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

API = "https://api.crossref.org/works"


def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def toks(s):
    return set(fold(s).split())


def query(title, author, year, mailto, rows=5):
    p = {"query.bibliographic": title, "rows": str(rows),
         "select": "DOI,title,author,issued,container-title,type,score"}
    if author:
        p["query.author"] = author
    url = API + "?" + urllib.parse.urlencode(p) + "&mailto=" + mailto
    ua = "research-stack/1.0"
    if mailto:
        ua += " (mailto:%s)" % mailto
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)["message"]["items"]


def best(items, want_title, want_author, want_year):
    wt = toks(want_title)
    scored = []
    for it in items:
        t = (it.get("title") or [""])[0]
        overlap = len(wt & toks(t)) / max(len(wt), 1)
        auths = it.get("author") or []
        first = fold(auths[0].get("family", "")) if auths else ""
        wanted_first = fold(want_author)
        # An omitted author means "no author gate", not a match on two empty
        # strings. When supplied, require a non-empty Crossref family name.
        amatch = not wanted_first or bool(first) and (
            wanted_first in first or first in wanted_first
        )
        yr = None
        try:
            yr = it["issued"]["date-parts"][0][0]
        except Exception:
            pass
        ydiff = abs((yr or 0) - (want_year or 0)) if yr and want_year else 9
        scored.append((overlap + (0.25 if amatch else 0) - min(ydiff, 3) * 0.08,
                       overlap, amatch, yr, t, it))
    scored.sort(key=lambda x: -x[0])
    return scored[0] if scored else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wanted")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--mailto", default=(os.environ.get("OPENALEX_EMAIL") or
                                          os.environ.get("ZOTERO_USER_EMAIL") or ""))
    ap.add_argument("--threshold", type=float, default=0.55)
    a = ap.parse_args()

    wanted = json.load(open(a.wanted, encoding="utf-8"))
    resolved, unresolved = {}, []
    for w in wanted:
        try:
            items = query(w["title"], w.get("author"), w.get("year"), a.mailto)
        except Exception as e:
            print("  ERR %-26s %s" % (w["id"], e))
            unresolved.append({**w, "reason": "crossref error: %s" % e})
            time.sleep(1.0)
            continue
        b = best(items, w["title"], w.get("author"), w.get("year"))
        if not b:
            unresolved.append({**w, "reason": "no crossref hit"})
            print("  MISS %-26s (no hit)" % w["id"])
            time.sleep(0.4)
            continue
        score, overlap, amatch, yr, title, it = b
        # A year gate is not optional: a reprint/conference version of the same
        # work scores 1.00 on title overlap and the same author, so overlap
        # alone happily returns the wrong edition (Saaty 1994 ISAHP for the
        # 2008 IJSS article). Anything outside +/-2 years needs a human look.
        ydiff = abs((yr or 0) - (w.get("year") or 0)) if yr and w.get("year") else 99
        ok = overlap >= a.threshold and amatch and ydiff <= 2
        flag = "OK  " if ok else "CHK "
        print("  %s%-26s ov=%.2f auth=%s yr=%s(d%s) doi=%s\n        -> %s"
              % (flag, w["id"], overlap, "y" if amatch else "n", yr,
                 ydiff if ydiff < 99 else "?", it.get("DOI"), title[:95]))
        rec = {"doi": it.get("DOI"), "crossref_title": title, "year": yr,
               "overlap": round(overlap, 3), "author_match": amatch,
               "type": it.get("type"), "wanted": w}
        if ok:
            resolved[w["id"]] = rec
        else:
            unresolved.append({**w, "reason": "low confidence", "candidate": rec})
        time.sleep(0.4)

    os.makedirs(a.out_dir, exist_ok=True)
    json.dump(resolved, open(os.path.join(a.out_dir, "resolved.json"), "w"),
              ensure_ascii=False, indent=1)
    json.dump(unresolved, open(os.path.join(a.out_dir, "unresolved.json"), "w"),
              ensure_ascii=False, indent=1)
    json.dump([r["doi"] for r in resolved.values() if r.get("doi")],
              open(os.path.join(a.out_dir, "dois.json"), "w"), indent=1)
    print("\nresolved=%d  unresolved=%d  -> %s"
          % (len(resolved), len(unresolved), a.out_dir))


if __name__ == "__main__":
    main()

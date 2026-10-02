#!/usr/bin/env python3
"""Fetch a consultant.ru document, following its per-chapter split.

consultant.ru serves a long act as a table of contents plus one page per
chapter (/document/<doc_id>/<40-hex>/). Rendering only the root page yields a
TOC that *looks* like a document — it has the right title and a plausible size,
but contains no normative text. This walks the chapter pages and reassembles.

Every page is rendered by fetch_pdf.sh (the container chromium); which route
answers on a given day is in ROUTES.md.

Usage:
  fetch_consultant.py <doc_id> <outfile.md> [--grep REGEX] [--limit N]

  --grep  keep only chapters whose rendered text matches REGEX (e.g. an article
          number), instead of all of them.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

FETCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_pdf.sh")
BASE = "https://www.consultant.ru/document/%s/"


def render(url, outdir, base):
    env = dict(os.environ)
    env.setdefault("FETCH_PDF_BUDGET", "22000")
    subprocess.run([FETCH, url, outdir, base], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=200)
    p = os.path.join(outdir, base + ".md")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def clean(md):
    """Drop the page markers and the site's link furniture."""
    md = re.sub(r"^\{\d+\}-+$", "", md, flags=re.M)
    md = re.sub(r"\[([^\]]*)\]\(https?://[^)]*\)", r"\1", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


# A failed render still produces a perfectly well-formed markdown file — the
# browser's own error page. Written into the corpus it looks like a short
# chapter, and only reading it reveals there is no law in it.
FAILED_RENDER = re.compile(
    r"site can.t be reached|ERR_TIMED_OUT|ERR_CONNECTION|ERR_NAME_NOT_RESOLVED"
    r"|took too long to respond|checking your browser|just a moment"
    r"|Доступ ограничен",
    re.I)


def looks_failed(txt):
    return bool(FAILED_RENDER.search(txt)) or len(txt) < 250


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id")
    ap.add_argument("outfile")
    ap.add_argument("--grep", help="filter on RENDERED chapter text (expensive: "
                                   "renders every chapter)")
    ap.add_argument("--toc-grep", help="filter on the TOC's own link LABEL — "
                                       "use this on big codes, where rendering "
                                       "all 600+ articles to find one is hours "
                                       "of work for a label match")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="consultant_")
    root = BASE % a.doc_id
    print("[toc] %s" % root, file=sys.stderr)
    toc = render(root, tmp, "toc")
    if not toc:
        sys.exit("TOC render failed for %s" % a.doc_id)

    link_re = (r"\[([^\]]*)\]\((https://www\.consultant\.ru/document/%s/"
               r"[0-9a-f]{20,}/)\)" % a.doc_id)
    labelled = re.findall(link_re, toc)
    if a.toc_grep:
        picked, seen = [], set()
        for label, u in labelled:
            if re.search(a.toc_grep, label, re.I) and u not in seen:
                seen.add(u)
                picked.append(u)
                print("[toc] match: %s" % label.strip()[:90], file=sys.stderr)
        urls = picked
        print("[toc] %d of %d chapter links match --toc-grep"
              % (len(urls), len(labelled)), file=sys.stderr)
    else:
        urls = sorted(set(re.findall(
            r"https://www\.consultant\.ru/document/%s/[0-9a-f]{20,}/" % a.doc_id,
            toc)))
        print("[toc] %d chapter urls" % len(urls), file=sys.stderr)
    if a.limit:
        urls = urls[:a.limit]
    if not urls:
        sys.exit("no chapter urls selected (toc had %d labelled links)"
                 % len(labelled))

    parts, kept, failed = [], 0, []
    for i, u in enumerate(urls, 1):
        txt = clean(render(u, tmp, "ch%03d" % i))
        for attempt in range(2):
            if not looks_failed(txt):
                break
            print("  ch%03d failed render, retry %d" % (i, attempt + 1),
                  file=sys.stderr)
            txt = clean(render(u, tmp, "ch%03d_r%d" % (i, attempt)))
        if looks_failed(txt):
            failed.append(u)
            print("  ch%03d FAILED — not written (%d chars)" % (i, len(txt)),
                  file=sys.stderr)
            continue
        if not txt:
            print("  ch%03d EMPTY" % i, file=sys.stderr)
            continue
        if a.grep and not re.search(a.grep, txt, re.I):
            print("  ch%03d skip (%d chars)" % (i, len(txt)), file=sys.stderr)
            continue
        kept += 1
        print("  ch%03d keep (%d chars)" % (i, len(txt)), file=sys.stderr)
        parts.append("\n\n{%d}%s\n\n%s" % (kept, "-" * 48, txt))

    if not parts:
        sys.exit("no chapters kept — check --grep")
    with open(a.outfile, "w", encoding="utf-8") as fh:
        fh.write("*Источник: %s — %d из %d разделов, собрано постранично*\n"
                 % (root, kept, len(urls)))
        fh.write("".join(parts) + "\n")
    total = sum(len(p) for p in parts)
    # Report what was dropped by name. A silent count of kept chapters reads
    # as "everything came through" when a third of it timed out.
    status = "OK" if not failed else "PARTIAL"
    print("%s %s  chapters=%d/%d chars=%d" % (status, a.outfile, kept,
                                              len(urls), total))
    for u in failed:
        print("  MISSING: %s" % u)


if __name__ == "__main__":
    main()

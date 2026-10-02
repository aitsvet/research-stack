#!/usr/bin/env python3
"""Fetch a consolidated Russian act from the pravo.gov.ru IPS as markdown.

consultant.ru is the obvious source and the unreliable one: it splits a code
into per-chapter pages behind a table of contents that intermittently renders
with no links at all, and curl hangs on its document paths outright. The
official IPS at pravo.gov.ru serves the same acts as a single consolidated
export — `?savertf=&nd=<id>&page=all` — which is one request, needs no browser,
and carries the amendment history in its header.

The export is MHTML wrapping cp1251 HTML. Two details matter:

  * Article numbers with a superscript index (141¹, 429²) carry that index in a
    `<span class="W9">`. Strip the tags naively and 141¹ becomes "1411", which
    reads as a different article and is invisible in a grep for "141.1". The
    span is converted to ".N" before the HTML goes through html_md.
  * The IPS wants a Referer from the document page, and answers over the SOCKS
    proxy but not always without it.

Document ids (`nd=`) are IPS-internal; find one by opening the act on
pravo.gov.ru and reading it out of the URL.

    fetch_pravo_ips.py 102033239 "ГК РФ часть первая"

Writes `<name>.md` (text) and `<name>.mht` (the untouched export) so the
extraction can be redone without refetching.
"""
import argparse
import email
import os
import re
import subprocess
import sys

import html_md

IPS = "http://pravo.gov.ru/proxy/ips/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")


def fetch(nd, out, proxy):
    page = "%s?docbody=&nd=%s" % (IPS, nd)
    cmd = ["curl", "-sS", "--max-time", "300", "-A", UA, "-e", page]
    if proxy:
        cmd += ["--socks5-hostname", proxy]
    cmd += ["%s?savertf=&nd=%s&page=all" % (IPS, nd), "-o", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode or not os.path.exists(out) or os.path.getsize(out) < 10000:
        sys.exit("fetch failed for nd=%s: %s" % (nd, r.stderr.strip()))
    return os.path.getsize(out)


def to_markdown(mht_path):
    msg = email.message_from_bytes(open(mht_path, "rb").read())
    parts = [x for x in msg.walk() if x.get_content_type() == "text/html"]
    if not parts:
        sys.exit("no text/html part in the export")
    t = parts[0].get_payload(decode=True).decode("cp1251", "replace")
    t = re.sub(r'(?is)<span class="W9">\s*(\d+)\s*</span>', r".\1", t)
    return html_md.html_to_md(t).replace("\xa0", " ") + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nd", help="IPS document id, e.g. 102033239 for ГК ч. 1")
    ap.add_argument("name", help="output basename, without extension")
    ap.add_argument("--proxy", default=os.environ.get("SOCKS_PROXY",
                                                      "localhost:3333"))
    a = ap.parse_args()

    mht = a.name + ".mht"
    size = fetch(a.nd, mht, a.proxy)
    text = to_markdown(mht)
    open(a.name + ".md", "w", encoding="utf-8").write(text)

    arts = re.findall(r"^Статья (\d+(?:\.\d+)?)\.", text, re.M)
    # A silently truncated export still parses and still looks like an act, so
    # report what is actually in it rather than just that a file was written.
    print("%s.mht %d B -> %s.md %d B" % (a.name, size, a.name, len(text)))
    print("articles: %d (first %s, last %s)"
          % (len(arts), arts[0] if arts else "-", arts[-1] if arts else "-"))
    if re.search(r'class="W9"|<span', text):
        print("WARNING: markup leaked into the text")


if __name__ == "__main__":
    main()

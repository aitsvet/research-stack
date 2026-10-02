#!/usr/bin/env python3
"""Fetch the FULL text of a kremlin.ru act, following its page/N chain.

kremlin.ru paginates act text: /acts/bank/<id> shows page 1 and links
/acts/bank/<id>/page/2 ... A single render captures only page 1 and stops
mid-word, which is invisible to a size check (every page is ~35 KB).

Drives the ALREADY-RUNNING proxied Chromium on :9222 through cdp_eval.Browser
(launch_chromium.sh with CHROMIUM_PROXY) — kremlin.ru refuses curl and blocks
datacenter exits, but answers that browser.

Usage: fetch_kremlin_act.py <outdir> <id>:<slug> [<id>:<slug> ...]
"""
import os
import re
import sys
import time

import cdp_eval

BODY_SEL = ".read__internal_content"
MAX_PAGES = 60


class Browser(cdp_eval.Browser):
    def __init__(self):
        super().__init__(timeout=60)

    def goto(self, url, settle=6.0):
        self.cmd("Page.navigate", url=url)
        # Poll for the body container instead of a blind sleep, but keep a
        # floor: kremlin.ru through the proxy is slow to settle.
        deadline = time.time() + 45
        time.sleep(2.0)
        while time.time() < deadline:
            n = self.js("document.querySelector(%r) ? "
                        "document.querySelector(%r).innerText.length : 0"
                        % (BODY_SEL, BODY_SEL))
            if n and n > 200:
                break
            time.sleep(1.5)
        time.sleep(settle * 0.2)


def fetch_act(br, act_id):
    """Return (title, date_line, [page_texts])."""
    base = "http://www.kremlin.ru/acts/bank/%s" % act_id
    br.goto(base)
    title = br.js("document.title") or ""
    pages = []
    url = base
    seen_urls = {base.split("#")[0]}
    seen_text = set()
    for n in range(1, MAX_PAGES + 1):
        if n > 1:
            br.goto(url)
        txt = br.js("(document.querySelector(%r)||{}).innerText || ''" % BODY_SEL)
        if not txt or not txt.strip():
            print("    page %d: EMPTY (%s)" % (n, url), file=sys.stderr)
            break
        txt = txt.strip()
        # The last page's only page/N link is a share anchor pointing back at
        # itself, so identical content would otherwise be appended twice.
        if txt in seen_text:
            print("    page %d: DUPLICATE of an earlier page — stopping"
                  % n, file=sys.stderr)
            break
        seen_text.add(txt)
        pages.append(txt)
        # Pick the LOWEST page number strictly greater than the current one.
        # Taking the last matching anchor grabs a self/share link on the final
        # page and silently truncates a multi-page act at page 2.
        nxt = br.js(
            "(function(){"
            "var re=/\\/acts\\/bank\\/%s\\/page\\/(\\d+)/;"
            "var best=null,bn=1e9;"
            "[...document.querySelectorAll('a')].forEach(function(x){"
            "  var m=(x.href||'').match(re); if(!m) return;"
            "  var n=parseInt(m[1],10);"
            "  if(n>%d && n<bn){bn=n;best=x.href.split('#')[0];}"
            "});"
            "return best||'';})()" % (act_id, n))
        nxt = (nxt or "").split("#")[0]
        print("    page %d: %d chars  next=%s" % (n, len(txt), nxt or "-"),
              file=sys.stderr)
        if not nxt or nxt in seen_urls:
            break
        seen_urls.add(nxt)
        url = nxt
    return title, pages


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    br = Browser()
    for spec in sys.argv[2:]:
        act_id, _, slug = spec.partition(":")
        slug = slug or act_id
        print("[kremlin] %s -> %s" % (act_id, slug), file=sys.stderr)
        title, pages = fetch_act(br, act_id)
        if not pages:
            print("  FAIL %s (no text)" % act_id)
            continue
        out = os.path.join(outdir, slug + ".md")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("# %s\n\n" % title.replace(" • Президент России", "").strip())
            fh.write("*Источник: http://www.kremlin.ru/acts/bank/%s "
                     "(официальная публикация, полный текст, "
                     "%d стр. постраничной навигации сайта)*\n" % (act_id, len(pages)))
            for i, p in enumerate(pages, 1):
                fh.write("\n\n{%d}%s\n\n" % (i, "-" * 48))
                fh.write(p)
            fh.write("\n")
        total = sum(len(p) for p in pages)
        # Differentiating completeness check: a federal act ends with the
        # signature block of the head of state. A size check cannot tell a
        # truncated act from a complete one — every kremlin page is ~19.5 KB.
        tail = pages[-1][-400:]
        signed = bool(re.search(r"Президент\s+Российской\s+Федерации", tail))
        print("  %s %s  pages=%d chars=%d signature=%s -> %s"
              % ("OK " if signed else "PARTIAL", act_id, len(pages), total,
                 "yes" if signed else "NO — text stops before the signature",
                 out))


if __name__ == "__main__":
    main()

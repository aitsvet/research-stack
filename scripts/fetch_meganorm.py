#!/usr/bin/env python3
"""Fetch a document from meganorm by slug, preferring the scanned official
edition and falling back to the page's own full text.

meganorm keeps two representations. `/mega_doc/norm/prikaz/<dir>/<slug>.html`
is the full text as HTML — always present, and the better source when it is.
`/Data2/<a>/<b>.pdf` is the scanned official edition, linked only from some
pages. The `<dir>` segment is not derivable from the document number, so the
plausible range is walked until a page answers. A Referer is required or the
PDF request 403s.

    fetch_meganorm.py <slug> <out-basename>     # writes .md, and .pdf if linked
"""
import os, re, subprocess, sys
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
BASE = "https://meganorm.ru"


def curl(url, out=None, referer=BASE + "/"):
    cmd = ["curl", "-sL", "--socks5-hostname", "localhost:3333", "-A", UA,
           "-e", referer, "--max-time", "120", url]
    if out:
        cmd += ["-o", out, "-w", "%{http_code} %{size_download}"]
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    return subprocess.run(cmd, capture_output=True).stdout.decode("utf-8", "replace")


def page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    body = soup.body or soup
    out = [re.sub(r"\s+", " ", l).strip()
           for l in body.get_text("\n", strip=True).splitlines()]
    return "\n\n".join(l for l in out if l)


slug, out = sys.argv[1], sys.argv[2]
for d in range(15, 46):
    page = f"{BASE}/mega_doc/norm/prikaz/{d}/{slug}_rekomendatsii_po_standartizatsii.html"
    html = curl(page)
    if len(html) < 20000 or "не найдена" in html[:3000]:
        continue
    txt = page_text(html)
    title = re.sub(r"\s+", " ", (BeautifulSoup(html, "html.parser").title or "").get_text()
                   if BeautifulSoup(html, "html.parser").title else slug)[:300]
    open(out + ".md", "w", encoding="utf-8").write(
        f"# {title}\n\nИсточник: {page} (meganorm)\n\n{txt}\n")
    print(f"{page}\n  текст: {len(txt)} знаков -> {out}.md", file=sys.stderr)
    pdfs = re.findall(r"(/Data2/\d+/\d+\.pdf)", html)
    if pdfs:
        print("  pdf:", curl(BASE + pdfs[0], out + ".pdf", referer=page), file=sys.stderr)
    break
else:
    print("не найдено ни на одном из путей", file=sys.stderr)
    sys.exit(1)

#!/usr/bin/env python3
"""Full-document OCR of a PDF via a vLLM-hosted Chandra model -> paginated Markdown.

Renders each PDF page to a PNG (PyMuPDF), POSTs it to an OpenAI-compatible
`/v1/chat/completions` endpoint with the verbatim upstream Chandra OCR prompt,
receives HTML, converts that HTML to Markdown, and concatenates all pages with
marker-style `{N}` + 48-dash page separators (matching the existing corpus so
outputs diff cleanly against it).

Request shape: image_url + the fixed upstream prompt, temperature 0, top_p 0.1.
Keeps the whole page (not just tables). Uses only stdlib + PyMuPDF + bs4.

Domain-agnostic: any PDF in, one Markdown file out.

Usage:
    chandra_ocr.py <pdf> [--out out.md] [--url http://localhost:8000/v1]
                   [--model chandra] [--dpi 192] [--min-dim 1024]
                   [--max-tokens 11000] [--timeout 600] [--api-key KEY]
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

import fitz  # PyMuPDF

# Verbatim from upstream chandra/prompts.py. The model was trained against
# this exact wording; do not paraphrase.
OCR_PROMPT = """
OCR this image to HTML.

Only use these tags ['math', 'br', 'i', 'b', 'u', 'del', 'sup', 'sub', 'table', 'tr', 'td', 'p', 'th', 'div', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'ul', 'ol', 'li', 'input', 'a', 'span', 'img', 'hr', 'tbody', 'small', 'caption', 'strong', 'thead', 'big', 'code', 'chem'], and these attributes ['class', 'colspan', 'rowspan', 'display', 'checked', 'type', 'border', 'value', 'style', 'href', 'alt', 'align', 'data-bbox', 'data-label'].

Guidelines:
* Inline math: Surround math with <math>...</math> tags. Math expressions should be rendered in KaTeX-compatible LaTeX. Use display for block math.
* Tables: Use colspan and rowspan attributes to match table structure.
* Formatting: Maintain consistent formatting with the image, including spacing, indentation, subscripts/superscripts, and special characters.
* Images: Include a description of any images in the alt attribute of an <img> tag. Do not fill out the src property. Describe in detail inside the div tag. Also convert charts to high fidelity data, and convert diagrams to mermaid.
* Forms: Mark checkboxes and radio buttons properly.
* Text: join lines together properly into paragraphs using <p>...</p> tags.  Use <br> tags for line breaks within paragraphs, but only when absolutely necessary to maintain meaning.
* Chemistry: Use <chem>...</chem> tags for chemical formulas with reactive SMILES.
* Lists: Preserve indents and proper list markers.
* Use the simplest possible HTML structure that accurately represents the content of the block.
* Make sure the text is accurate and easy for a human to read and interpret.  Reading order should be correct and natural.
""".strip()

PAGE_SEP = "-" * 48  # marker-compatible {N} + 48 dashes


# HTML -> Markdown lives in html_md.py (shared with convert_office.py, which
# runs in a LibreOffice container and must not pull in PyMuPDF).
from html_md import html_to_md

# --------------------------------------------------------------------------- #
# Chandra endpoint
# --------------------------------------------------------------------------- #
def ocr_page(img_bytes, mime, url, model, api_key, max_tokens, timeout):
    b64 = base64.b64encode(img_bytes).decode("ascii")
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }],
        "temperature": 0.0,
        "top_p": 0.1,
        "max_tokens": max_tokens,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def render_jpeg(page, dpi, min_dim, max_dim, quality, shrink=1.0):
    """Render to JPEG. Resolution = max(dpi, min-dim-driven dpi), but the
    longest side is capped at max_dim px (keeps vision-token count and request
    body bounded so we don't OOM/overflow the remote model). `shrink` (<=1.0)
    scales everything down further for progressive retries. JPEG keeps the body
    ~10x smaller than PNG at legible quality."""
    pt_min = min(page.rect.width, page.rect.height)
    pt_max = max(page.rect.width, page.rect.height)
    scale = max((min_dim / pt_min), dpi / 72.0)          # px-per-pt
    if pt_max * scale > max_dim:                          # cap longest side
        scale = max_dim / pt_max
    scale *= shrink
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
    return pix.tobytes(output="jpg", jpg_quality=quality), pix.width, pix.height


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None, help="output .md (default: <pdf>.md)")
    ap.add_argument("--url", default=os.environ.get("CHANDRA_URL", "http://localhost:8000/v1"))
    ap.add_argument("--model", default=os.environ.get("CHANDRA_MODEL", "chandra"))
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--min-dim", type=int, default=1000)
    ap.add_argument("--max-dim", type=int, default=1600,
                    help="cap longest image side in px (bounds vision tokens)")
    ap.add_argument("--jpeg-quality", type=int, default=88)
    ap.add_argument("--max-tokens", type=int, default=9000)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--api-key", default=os.environ.get("CHANDRA_API_KEY", ""))
    a = ap.parse_args()

    out_path = a.out or (os.path.splitext(a.pdf)[0] + ".md")
    doc = fitz.open(a.pdf)
    n = len(doc)
    print(f"[chandra] {a.pdf}: {n} page(s) -> {out_path}", file=sys.stderr)
    pages_md = []
    for i, page in enumerate(doc):
        html = None
        # progressive fallback if the endpoint rejects size/context
        for shrink, mtok in ((1.0, a.max_tokens), (0.8, min(a.max_tokens, 7000)),
                             (0.65, 5000)):
            try:
                jpg, w, h = render_jpeg(page, a.dpi, a.min_dim, a.max_dim,
                                        a.jpeg_quality, shrink)
                kb = len(jpg) // 1024
                print(f"[chandra] page {i + 1}/{n} {w}x{h} jpg={kb}KB "
                      f"shrink={shrink} ...", file=sys.stderr)
                html = ocr_page(jpg, "jpeg", a.url, a.model, a.api_key, mtok, a.timeout)
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")[:300]
                print(f"[chandra] page {i + 1} HTTP {e.code}: {msg} -> retry smaller",
                      file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"[chandra] page {i + 1} {type(e).__name__}: {e} -> retry smaller",
                      file=sys.stderr)
        md = html_to_md(html) if html else "*[OCR FAILED FOR THIS PAGE]*"
        pages_md.append(f"{{{i}}}{PAGE_SEP}\n\n{md}")
    doc.close()

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n\n" + "\n\n".join(pages_md) + "\n")
    print(f"[chandra] wrote {out_path} ({len(pages_md)} pages)", file=sys.stderr)


if __name__ == "__main__":
    main()

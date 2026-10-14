#!/usr/bin/env python3
"""Add a page-level invisible text layer from paginated Markdown to a PDF.

Keeps the original page artwork. Text search locates the correct page;
highlight positions follow the fitted text, not individual scanned words.
Accepts contiguous zero- or one-based {N} + 48-dash page markers.
"""
import argparse
from collections import Counter
import os
from pathlib import Path
import re
import tempfile

from bs4 import BeautifulSoup
import fitz
import markdown

import pagemd


def compact(text):
    # Ignore line wrapping, soft hyphens and our explicit direction marks.
    text = re.sub(r"\s+", "", text).replace("\u00ad", "").replace("\u200e", "")
    # MuPDF may move Arabic vowel marks across a bidirectional text run.
    # Compare base text in order and verify every vowel mark separately.
    return re.sub(r"[\u064b-\u065f\u0670]", "", text)


def same_text(left, right):
    return (compact(left) == compact(right) and
            Counter(re.findall(r"[\u064b-\u065f\u0670]", left)) ==
            Counter(re.findall(r"[\u064b-\u065f\u0670]", right)))


def add_layer(source, text_file, output, font_file):
    source, text_file, output = map(Path, (source, text_file, output))
    if output.exists():
        raise ValueError("Output already exists")
    body = text_file.read_text(encoding="utf-8")
    if "OCR FAILED" in body:
        raise ValueError("OCR contains failed pages")
    ids, bodies = pagemd.split(body)
    texts = [BeautifulSoup(markdown.markdown(t, extensions=["tables"]),
                           "html.parser").get_text(" ", strip=True)
             for t in bodies]
    with fitz.open(source) as doc:
        if not ids or len(ids) != len(doc) or ids != list(range(ids[0], ids[0] + len(doc))) or ids[0] not in (0, 1):
            raise ValueError("Page markers must cover every PDF page in order")
        for page, text in zip(doc, texts):
            if not text:
                raise ValueError(f"Empty OCR page {page.number + 1}")
            rect = page.rect + (12, 12, -12, -12)
            size = 10.0
            # Isolate Arabic runs on their own lines so MuPDF's extraction
            # reverses visual glyph order back to logical Unicode order.
            # LRM restores the direction of following neutral punctuation.
            drawing = re.sub(r"[\u0600-\u06ff][\u0600-\u06ff ]*",
                             lambda m: "\n" + m[0].strip()[::-1] + "\n\u200e", text)
            font = fitz.Font(fontfile=str(font_file))
            for run in re.findall(r"[\u0600-\u06ff][\u0600-\u06ff ]*", text):
                width = font.text_length(run.strip(), fontsize=1)
                size = min(size, rect.width / max(width, 1) * 0.95)
            while True:
                spare = page.insert_textbox(rect, drawing, fontname="ocrtext",
                                            fontfile=str(font_file), fontsize=size,
                                            render_mode=3)
                if spare >= 0:
                    break
                size *= 0.85
                if size < 1:
                    raise ValueError(f"Cannot fit OCR page {page.number + 1}")
            if not same_text(page.get_text(), text):
                raise ValueError(f"Text round-trip differs on page {page.number + 1}")
        doc.subset_fonts()
        fd, staged = tempfile.mkstemp(dir=output.parent, suffix=".pdf")
        os.close(fd)
        try:
            doc.save(staged, deflate=True, garbage=3)
            with fitz.open(staged) as check:
                if len(check) != len(texts):
                    raise ValueError("Saved PDF page count differs")
                for page, text in zip(check, texts):
                    if not same_text(page.get_text(), text):
                        raise ValueError(f"Saved text differs on page {page.number + 1}")
            os.link(staged, output)  # refuse an output created during this run
        finally:
            os.unlink(staged)
    print(f"{output}: {len(texts)} searchable pages")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("markdown")
    parser.add_argument("output")
    parser.add_argument("--font", required=True, help="Unicode TTF font covering the source language")
    args = parser.parse_args()
    add_layer(args.pdf, args.markdown, args.output, args.font)

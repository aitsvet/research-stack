"""Page marks of paginated Markdown — the corpus convention shared by every
writer (chandra_ocr, extract_texts, fetch_consultant, fetch_kremlin_act) and
reader (extract_texts, extract_tables, pdf_text_layer).

A page starts with `{N}` followed by 48 dashes on a line of its own (marker's
format, so outputs diff cleanly against older extractions).
"""
import re

DASHES = 48
MARK = re.compile(r"^\{(\d+)\}-{%d}\s*$" % DASHES, re.M)


def mark(n):
    """The mark line for page `n`, without surrounding newlines."""
    return "{%d}%s" % (n, "-" * DASHES)


def numbers(text):
    """Page numbers of every mark in `text`, in order."""
    return [int(n) for n in MARK.findall(text)]


def split(text):
    """(numbers, bodies): the marked pages of `text`; anything before the first
    mark is dropped."""
    parts = MARK.split(text)
    return [int(n) for n in parts[1::2]], parts[2::2]

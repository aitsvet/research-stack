"""Reading and rewriting .docx packages, shared by md_docx.py, merge_edits.py
and make_docx_template.py: a part's XML, the paragraphs of the body with their
plain text, and a copy of the package with some parts replaced.
"""
import re
import zipfile
from xml.sax.saxutils import unescape

DOCUMENT = "word/document.xml"
# `<w:p ` / `<w:p>` only: `<w:p/>` (an empty paragraph) has no closing tag and
# `<w:pPr>` is a property, not a paragraph.
_PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_TEXT = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)
_ENTITIES = {"&quot;": '"', "&apos;": "'"}


def part(path, name=DOCUMENT):
    """A package part as text."""
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8")


def paragraphs(xml):
    """(plain text, paragraph XML) for every paragraph of a document part."""
    return [(unescape("".join(_TEXT.findall(p)), _ENTITIES).strip(), p)
            for p in _PARA.findall(xml)]


def write(src, dst, parts):
    """Copy package `src` to `dst`, replacing the parts named in `parts`
    (str or bytes); every other part is copied byte for byte, in order."""
    with zipfile.ZipFile(src) as z, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for n in z.namelist():
            out.writestr(n, parts[n] if n in parts else z.read(n))

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from md_docx import build, check  # noqa: E402

TEMPLATE_PARTS = {
    "word/document.xml": (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>template</w:t></w:r></w:p></w:body></w:document>"
    ),
    "word/_rels/document.xml.rels": (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        'Target="media/image.png"/></Relationships>'
    ),
    "word/footnotes.xml": (
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:id="1"><w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p></w:footnote>'
        "</w:footnotes>"
    ),
    "word/styles.xml": (
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "</w:styles>"
    ),
    "docProps/core.xml": (
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>orig</dc:creator>'
        "<cp:lastModifiedBy>orig</cp:lastModifiedBy></cp:coreProperties>"
    ),
    "media/image.png": (
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x0dIHDR"
        + b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        + b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    ),
}

MD = """\
УДК <code>

# НАЗВАНИЕ СТАТЬИ

**Фамилия И.О.**

*магистрант,*
*Университет,*

*Научный руководитель: … .*
*Scientific adviser: … .*

**Аннотация**

Текст аннотации…

## Введение

Обычный абзац.
"""


def make_template(dst):
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in TEMPLATE_PARTS.items():
            z.writestr(name, data)


class MdDocxTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.template = self.tmp / "template.docx"
        make_template(self.template)
        self.md = self.tmp / "paper.md"
        self.md.write_text(MD, encoding="utf8")
        self.out = self.tmp / "paper.docx"

    def tearDown(self):
        self._tmp.cleanup()

    def _body(self):
        return zipfile.ZipFile(self.out).read("word/document.xml").decode("utf8")

    def test_default_export_matches_markdown(self):
        n, adviser, figure = build(str(self.md), str(self.template), str(self.out), 15)
        self.assertGreater(n, 0)
        self.assertIsNotNone(adviser)
        d, m, bad = check(str(self.md), str(self.out))
        self.assertEqual(bad, 0, "exported body must match the Markdown master")

    def test_udc_line_is_left_aligned(self):
        build(str(self.md), str(self.template), str(self.out), 15)
        body = self._body()
        para = body[body.index("<w:p") : body.index("</w:p>") + 6]
        self.assertIn('<w:jc w:val="left" />', para)
        self.assertIn("УДК", para)

    def test_title_renders_bold_italic(self):
        build(str(self.md), str(self.template), str(self.out), 15)
        body = self._body()
        self.assertIn("<w:b />", body)
        self.assertIn("<w:i />", body)
        self.assertNotIn('w:lineRule="atLeast"', body)

    def test_core_creator_is_author(self):
        build(str(self.md), str(self.template), str(self.out), 15)
        core = zipfile.ZipFile(self.out).read("docProps/core.xml").decode("utf8")
        self.assertIn("<dc:creator>Фамилия И.О.</dc:creator>", core)


if __name__ == "__main__":
    unittest.main()

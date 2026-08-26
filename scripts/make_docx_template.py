#!/usr/bin/env python3
"""Accepted .docx -> reusable, text-free export template.

md_docx.py does not generate a .docx package; it keeps every part of a template
(styles, theme, section properties, relationships, footnote plumbing, image
relationship id) and replaces only the body. That makes the template a build
input: without it nothing exports, so it has to live in version control.

Committing the accepted submission itself is the wrong way to get there. It
carries the whole manuscript and its figure, which is a hundred times the bytes
and puts a paper's text into whatever repository holds the template.

This strips one to the parts that carry format and nothing else. The body is
reduced to an empty paragraph plus the original sectPr, footnote 1 is emptied
but kept as the slot md_docx.py fills with the adviser line, the figure becomes
a 1x1 placeholder that the next export overwrites, and docProps metadata is
cleared. Typical result is a few kilobytes with no sentence of the original.

    make_docx_template.py ACCEPTED.docx TEMPLATE.docx

Prove the result before relying on it: export the same Markdown through the old
and the new template and compare the PDFs on page count, page size, extracted
text, font set and sectPr. They must be identical.
"""
import re
import struct
import sys
import zipfile
import zlib


def placeholder_png():
    def chunk(tag, data):
        c = tag + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)
    ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(b'\x00\x00')) + chunk(b'IEND', b''))


def strip(src, dst):
    z = zipfile.ZipFile(src)
    doc = z.read('word/document.xml').decode('utf8')
    sect = re.search(r'<w:sectPr\b.*?</w:sectPr>', doc, re.S)
    if not sect:
        sys.exit('template has no sectPr; page format cannot be preserved')
    head = doc[:doc.index('<w:body>') + len('<w:body>')]
    body = head + '<w:p/>' + sect.group(0) + '</w:body></w:document>'

    parts = {'word/document.xml': body.encode('utf8')}

    if 'word/footnotes.xml' in z.namelist():
        fn = z.read('word/footnotes.xml').decode('utf8')
        fn = re.sub(r'(<w:footnote w:id="1">).*?(</w:footnote>)',
                    r'\1<w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p>\2',
                    fn, flags=re.S)
        parts['word/footnotes.xml'] = fn.encode('utf8')

    if 'docProps/core.xml' in z.namelist():
        core = z.read('docProps/core.xml').decode('utf8')
        for tag in ('dc:title', 'dc:creator', 'cp:lastModifiedBy', 'dc:description', 'cp:keywords'):
            core = re.sub(rf'<{tag}>.*?</{tag}>', f'<{tag}></{tag}>', core, flags=re.S)
        parts['docProps/core.xml'] = core.encode('utf8')

    if 'media/image.png' in z.namelist():
        parts['media/image.png'] = placeholder_png()

    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as out:
        for n in z.namelist():
            out.writestr(n, parts.get(n, z.read(n)))

    left = zipfile.ZipFile(dst).read('word/document.xml').decode('utf8')
    words = len(re.findall(r'<w:t[^>]*>([^<]+)</w:t>', left))
    print(f'{dst}: {len(z.namelist())} parts, {words} text runs left in the body')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-3].strip())
    strip(sys.argv[1], sys.argv[2])

#!/usr/bin/env python3
"""Markdown article -> journal-formatted .docx, by rebuilding a template's body.

Journals commonly want one fixed shape: A4, 2 cm margins, Times New
Roman 14 pt, 1.5 spacing, 1.25 cm first-line indent, justified body, centred
title and figures, the scientific adviser in a footnote on the author's name.
Reproducing that from scratch means hand-writing styles.xml, theme, numbering
and content types, and a single wrong child-element order silently breaks Word.

So this script does not generate a package. It takes a .docx that a journal has
already accepted, keeps every part of it (styles, theme, section properties,
relationships, footnote plumbing, image relationship id) and replaces only the
body paragraphs with ones built from the Markdown. Fidelity is then a property
of the template, not of this code.

    md_docx.py PAPER.md --template ACCEPTED.docx [--out OUT.docx]
                        [--image-cm 15] [--check]

--check re-reads the result and diffs its paragraph text against the Markdown;
it is the only cheap proof that the export says what the master says.

Markdown handled: `# ` title, `## `/`### ` headings, `**bold**`, `*italic*`,
`==marked==` (yellow highlight, for showing an editor what changed),
`- ` bullets (rendered as en-dash paragraphs, the house convention), numbered
bibliography entries, `![alt](fig.png)` and a `*Рис. N ...*` caption.

Front matter is everything before the first `## `, and there each line is one
paragraph: `**Name**` renders bold italic (the first one carries the footnote
reference and becomes dc:creator), `**Аннотация**`/`**Abstract**` render bold
upright as labels, `*text*` renders italic. Adviser lines, Russian and English,
are lifted out of the body and joined into the single footnote.

Traps worth knowing:
  * rPr children are order-sensitive: rFonts, b, i, sz. b after i fails schema
    validation with a message that names the wrong element.
  * officecli keeps a resident process per file; after writing the .docx from
    outside, `officecli close FILE` before validating or you validate the
    previous bytes.
  * LibreOffice in the docconv image needs -env:UserInstallation or it dies
    with "User installation could not be completed".
"""
import argparse
import io
import os
import re
import sys
import zipfile

TNR = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="Times New Roman" />'
SECT = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838" w:orient="portrait" />'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" />'
        '<w:docGrid w:type="default" /></w:sectPr>')
INDENT = 709            # 1.25 cm first line
LINE = 360              # 1.5 spacing
FRONT_LABELS = {'Аннотация', 'Abstract', 'Резюме', 'Summary'}
FNREF = '<w:r><w:rPr><w:rStyle w:val="FootnoteReference" /></w:rPr><w:footnoteReference w:id="1" /></w:r>'


def rpr(bold=False, italic=False, mark=False):
    # rPr children are order-sensitive: rFonts, b, i, sz, highlight.
    return (TNR + ('<w:b />' if bold else '') + ('<w:i />' if italic else '')
            + '<w:sz w:val="28" />' + ('<w:highlight w:val="yellow" />' if mark else ''))


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def runs(text, italic=False):
    out = []
    for chunk in re.split(r'(==[^=]+==)', text):
        if not chunk:
            continue
        mark = chunk.startswith('==') and chunk.endswith('==')
        if mark:
            chunk = chunk[2:-2]
        for part in re.split(r'(\*\*[^*]+\*\*|\*[^*]+\*)', chunk):
            if not part:
                continue
            if part.startswith('**') and part.endswith('**'):
                b, i, body = True, italic, part[2:-2]
            elif part.startswith('*') and part.endswith('*'):
                b, i, body = False, True, part[1:-1]
            else:
                b, i, body = False, italic, part
            out.append('<w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
                       % (rpr(b, i, mark), esc(body)))
    return ''.join(out)


def ppr(before=0, after=0, ind=0, jc='both'):
    return ('<w:pPr><w:spacing w:before="%d" w:after="%d" w:line="%d" w:lineRule="auto" />'
            '<w:ind w:firstLine="%d" /><w:jc w:val="%s" /></w:pPr>' % (before, after, LINE, ind, jc))


def image_runs(name, cx, cy, rid):
    return ('<w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve" /></w:r>' % rpr() +
            '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="%d" cy="%d" /><wp:effectExtent l="0" t="0" r="0" b="0" />'
            '<wp:docPr id="1" name="%s" descr="" />'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1" /></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="%s" /><pic:cNvPicPr /></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="%s" cstate="print" /><a:stretch><a:fillRect /></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0" /><a:ext cx="%d" cy="%d" /></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst /></a:prstGeom></pic:spPr></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
            % (cx, cy, name, name, rid, cx, cy))


def png_size(path):
    import struct
    with open(path, 'rb') as fh:
        return struct.unpack('>II', fh.read(24)[16:24])


def build(md_path, template, out_path, image_cm):
    src_dir = os.path.dirname(os.path.abspath(md_path))
    lines = io.open(md_path, encoding='utf8').read().split('\n')
    zt = zipfile.ZipFile(template)
    rid = re.search(r'Type="[^"]*/image" Target="[^"]*" Id="([^"]+)"',
                    zt.read('word/_rels/document.xml.rels').decode('utf8'))
    rid = rid.group(1) if rid else None

    paras, adviser, figure, pid = [], [], None, 0x00100000
    front, titles, named = True, 0, False
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if re.match(r'^\*(Научный руководитель|Scientific advis[eo]r):', s):
            adviser.append(s.strip('*'))
            continue
        if s.startswith('## '):
            front = False
        m = re.match(r'^!\[[^\]]*\]\(([^)]+)\)$', s)
        lbl = re.match(r'^\*\*([^*]+)\*\*$', s)
        if m:
            if rid is None:
                sys.exit('template has no image relationship; cannot place a figure')
            figure = os.path.join(src_dir, m.group(1))
            w, h = png_size(figure)
            cx = int(image_cm * 360000)
            body = image_runs(os.path.basename(figure), cx, int(cx * h / w), rid)
            p = ppr(before=120, ind=0, jc='center') + body
        elif s.startswith('УДК'):
            p = ppr(jc='left') + runs(s)
        elif s.startswith('# '):
            titles += 1
            p = ppr(before=0 if titles == 1 else 240, ind=0, jc='center') + runs('**' + s[2:] + '**', italic=True)
        elif re.match(r'^\*Рис\.', s):
            p = ppr(after=120, ind=0, jc='center') + runs(s)
        elif front and lbl and lbl.group(1) in FRONT_LABELS:
            p = ppr(before=240, ind=0, jc='left') + runs(s)
        elif front and lbl:
            p = ppr(before=240, ind=0, jc='left') + runs(s, italic=True)
            if not named:
                p, named = p + FNREF, lbl.group(1)
        elif front and s.startswith('*') and s.endswith('*'):
            p = ppr(ind=0, jc='left') + runs(s)
        elif re.match(r'^## Библиографический', s):
            p = ppr(before=240, ind=0, jc='left') + runs('**' + s[3:] + '**')
        elif s.startswith('### '):
            p = ppr(before=120, ind=0, jc='left') + runs('**' + s[4:] + '**', italic=True)
        elif s.startswith('## '):
            p = ppr(before=240, after=120, ind=0, jc='left') + runs('**' + s[3:] + '**', italic=True)
        elif s.startswith('- '):
            p = ppr(ind=INDENT) + runs('– ' + s[2:])
        else:
            p = ppr(ind=INDENT) + runs(s)
        paras.append('<w:p w14:paraId="%08X" w14:textId="%08X">%s</w:p>' % (pid, pid + 1, p))
        pid += 2
    adviser = ' '.join(adviser) or None

    head = re.match(r'^(.*?<w:body>)', zt.read('word/document.xml').decode('utf8'), re.S).group(1)
    doc = head + ''.join(paras) + SECT + '</w:body></w:document>'

    core = None
    if named and 'docProps/core.xml' in zt.namelist():
        core = zt.read('docProps/core.xml').decode('utf8')
        for tag in ('dc:creator', 'cp:lastModifiedBy'):
            core = re.sub(r'<%s>[^<]*</%s>' % (tag, tag), '<%s>%s</%s>' % (tag, esc(named), tag), core)

    fn = zt.read('word/footnotes.xml').decode('utf8') if 'word/footnotes.xml' in zt.namelist() else None
    if fn and adviser:
        fn = re.sub(r'(<w:t xml:space="preserve">) [^<]*(</w:t>)',
                    lambda m: m.group(1) + ' ' + esc(adviser) + m.group(2), fn)

    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as out:
        for n in zt.namelist():
            if n == 'word/document.xml':
                out.writestr(n, doc.encode('utf8'))
            elif n == 'media/image.png' and figure:
                out.writestr(n, open(figure, 'rb').read())
            elif fn and n == 'word/footnotes.xml':
                out.writestr(n, fn.encode('utf8'))
            elif core and n == 'docProps/core.xml':
                out.writestr(n, core.encode('utf8'))
            else:
                out.writestr(n, zt.read(n))
    return len(paras), adviser, figure


def check(md_path, out_path):
    import difflib
    x = zipfile.ZipFile(out_path).read('word/document.xml').decode('utf8')
    got = []
    for p in re.findall(r'<w:p\b.*?</w:p>', x, re.S):
        t = ''.join(re.findall(r'<w:t[^>]*>(.*?)</w:t>', p, re.S))
        t = t.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        got.append('IMG' if '<w:drawing' in p else t.strip())

    def clean(s):
        s = s.strip()
        s = re.sub(r'^#{1,6}\s*', '', s).replace('**', '').replace('==', '')
        s = re.sub(r'^\*(.+)\*$', r'\1', s)
        return re.sub(r'^- ', '– ', s)

    tgt = []
    for l in io.open(md_path, encoding='utf8').read().split('\n'):
        s = l.strip()
        if not s or re.match(r'^\*(Научный руководитель|Scientific advis[eo]r):', s):
            continue
        tgt.append('IMG' if s.startswith('![') else clean(s))
    norm = lambda s: re.sub(r'\s+', ' ', s).strip()
    a, b = [norm(s) for s in got], [norm(s) for s in tgt]
    diffs = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != 'equal']
    for tag, i1, i2, j1, j2 in diffs:
        for k in range(i1, i2):
            print('  DOCX', a[k][:100])
        for k in range(j1, j2):
            print('  MD  ', b[k][:100])
    return len(a), len(b), len(diffs)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('markdown')
    ap.add_argument('--template', default=os.environ.get('DOCX_TEMPLATE'),
                    help='accepted .docx whose styles and section properties are reused')
    ap.add_argument('--out')
    ap.add_argument('--image-cm', type=float, default=15.0)
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    if not a.template:
        ap.error('--template (or $DOCX_TEMPLATE) is required')
    out = a.out or os.path.splitext(a.markdown)[0] + '.docx'
    n, adviser, figure = build(a.markdown, a.template, out, a.image_cm)
    print('%s: %d paragraphs%s%s' % (out, n,
                                     ', adviser footnote' if adviser else '',
                                     ', figure ' + os.path.basename(figure) if figure else ''))
    if a.check:
        d, m, bad = check(a.markdown, out)
        print('check: docx %d paragraphs, markdown %d, differences %d' % (d, m, bad))
        if bad:
            sys.exit(1)


if __name__ == '__main__':
    main()

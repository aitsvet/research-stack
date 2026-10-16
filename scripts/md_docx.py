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
`==marked==` (yellow highlight, or with --mark-fill a character shading of that
colour, for showing a reader which passages are additions),
`- ` bullets (rendered as en-dash paragraphs, the house convention),
`![alt](fig.png)` and a `*Рис. N ...*` caption. Numbered lines get no special
handling: `1. ` stays literal text in an ordinary body paragraph, which is what
a bibliography wants and why in-body enumerations use `- ` instead.

Pipe tables (`| a | b |` rows, `|---|` separator skipped) become a bordered
Word table: first row bold and repeated on each page, single-spaced cells at
--table-pt, column widths chosen to minimise the estimated table height. A line
opening `Таблица N`/`Table N` is a centred caption kept with the table, and
`Источник:`/`Source:` a flush-left note. The reference-list heading
(`## Библиографический список`, `## Список использованных источников`,
`## References`) is centred bold.

Venue layout knobs: --pt (body size), --line (spacing multiple), --table-pt,
--front-align right (author lines in the front matter), --page-numbers (a
centred PAGE field in the footer, for venues that require numbered pages).

Front matter is everything before the first `## `, and there each line is one
paragraph. `**Name**` renders bold italic and `*text*` renders italic. The first
name carries the footnote reference when adviser lines are present and becomes
dc:creator.
`**Аннотация**`/`**Abstract**` render bold upright as labels. Adviser lines,
Russian and English, are lifted out of the body and joined into one footnote.

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

import docxpkg

TNR = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="Times New Roman" />'
SECT = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838" w:orient="portrait" />'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" />'
        '<w:docGrid w:type="default" /></w:sectPr>')
INDENT = 709            # 1.25 cm first line
LINE = 360              # 1.5 spacing; --line overrides
LINE_RULE = 'auto'
SZ = 28                 # half-points, 14 pt; --pt overrides
TABLE_SZ = 28           # --table-pt overrides; defaults to --pt
FRONT_JC = 'left'       # author lines in the front matter; --front-align
MARK_FILL = None        # --mark-fill RRGGBB: shade ==marked== runs instead of highlighting
TEXT_W = 11906 - 2 * 1134
FOOTER_RID = 'rIdMdDocxFooter1'
FOOTER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
          '<w:p><w:pPr><w:jc w:val="center" /></w:pPr>'
          '<w:r><w:rPr>%s<w:sz w:val="%%d" /></w:rPr><w:fldChar w:fldCharType="begin" /></w:r>'
          '<w:r><w:rPr>%s<w:sz w:val="%%d" /></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
          '<w:r><w:rPr>%s<w:sz w:val="%%d" /></w:rPr><w:fldChar w:fldCharType="separate" /></w:r>'
          '<w:r><w:rPr>%s<w:sz w:val="%%d" /></w:rPr><w:t>1</w:t></w:r>'
          '<w:r><w:rPr>%s<w:sz w:val="%%d" /></w:rPr><w:fldChar w:fldCharType="end" /></w:r>'
          '</w:p></w:ftr>') % ((TNR,) * 5)
LIST_HEADING = re.compile(r'^## (Библиографический|Список (использованных )?(источников|литературы)'
                          r'|СПИСОК|References)', re.I)
FRONT_LABELS = {'Аннотация', 'Abstract', 'Резюме', 'Summary'}
FNREF = ('<w:r><w:rPr><w:rStyle w:val="FootnoteReference" />'
         '<w:vertAlign w:val="superscript" /></w:rPr>'
         '<w:footnoteReference w:id="1" /></w:r>')
FNSTYLE = ('<w:style w:type="character" w:styleId="FootnoteReference">'
           '<w:name w:val="footnote reference" /><w:uiPriority w:val="99" />'
           '<w:semiHidden /><w:unhideWhenUsed />'
           '<w:rPr><w:vertAlign w:val="superscript" /></w:rPr></w:style>')


def footnote_xml(lines):
    """Сноска руководителя: надстрочный номер, каждая строка своим абзацем."""
    mark = ('<w:r><w:rPr><w:rStyle w:val="FootnoteReference" />'
            '<w:vertAlign w:val="superscript" /></w:rPr><w:footnoteRef /></w:r>')
    out = ['<w:footnote w:id="1">']
    for i, line in enumerate(lines):
        text = (' ' if i == 0 else '') + esc(line)
        out.append('<w:p><w:pPr>%s<w:jc w:val="left" /></w:pPr>%s'
                   '<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
                   % ('<w:spacing w:line="240" w:lineRule="auto" />',
                      mark if i == 0 else '', text))
    out.append('</w:footnote>')
    return ''.join(out)


def rpr(bold=False, italic=False, mark=False, sz=None):
    # rPr children are order-sensitive: rFonts, b, i, sz, highlight, shd.
    # Highlight has a fixed palette of bright colours; shading takes any fill.
    if mark and MARK_FILL:
        mark_xml = '<w:shd w:val="clear" w:color="auto" w:fill="%s" />' % MARK_FILL
    else:
        mark_xml = '<w:highlight w:val="yellow" />' if mark else ''
    return (TNR + ('<w:b />' if bold else '') + ('<w:i />' if italic else '')
            + '<w:sz w:val="%d" />' % (sz or SZ) + mark_xml)


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def runs(text, italic=False, sz=None):
    out = []
    for chunk in re.split(r'(==(?:(?!==).)+==)', text):
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
                       % (rpr(b, i, mark, sz), esc(body)))
    return ''.join(out)


def ppr(before=0, after=0, ind=0, jc='both', line=None, keep_next=False):
    return ('<w:pPr>%s<w:spacing w:before="%d" w:after="%d" w:line="%d" w:lineRule="%s" />'
            '<w:ind w:firstLine="%d" /><w:jc w:val="%s" /></w:pPr>'
            % ('<w:keepNext />' if keep_next else '', before, after, line or LINE, LINE_RULE, ind, jc))


def table_cells(s):
    return [c.strip() for c in s.strip().strip('|').split('|')]


def wrapped_lines(text, width):
    """Lines a cell takes at TABLE_SZ: greedy word wrap with an average
    Times New Roman glyph of about half an em, inside 57-twip cell margins."""
    cap = max((width - 114) / (TABLE_SZ * 5.0), 1)
    n, cur = 1, 0
    for word in re.sub(r'\*\*|==', '', text).split():
        if cur and cur + len(word) + 1 > cap:
            n, cur = n + 1, len(word) + 1
        else:
            cur += len(word) + 1
    return n


def column_widths(rows, n):
    """Column widths that minimise the table's estimated height (sum over rows
    of the tallest cell). Starts from widths proportional to the text each
    column carries and moves 1/40 of the text width between columns while that
    shortens the table; no column drops below 0.6/n of the width."""
    weight = [max(sum(len(r[k]) for r in rows), 1) for k in range(n)]
    floor = 0.6 / n
    share = [max(w / sum(weight), floor) for w in weight]
    share = [x / sum(share) for x in share]

    def height(sh):
        return sum(max(wrapped_lines(c, TEXT_W * x) for c, x in zip(r, sh)) for r in rows)

    best, step = height(share), 1.0 / 40
    improved = n > 1
    while improved:
        improved = False
        for i in range(n):
            for j in range(n):
                if i == j or share[j] - step < floor:
                    continue
                trial = list(share)
                trial[i] += step
                trial[j] -= step
                h = height(trial)
                if h < best:
                    best, share, improved = h, trial, True
    return [int(TEXT_W * x) for x in share]


def table_xml(rows, pid):
    """Pipe table -> w:tbl: first row a bold repeated header, single-spaced
    cells at TABLE_SZ, column widths from column_widths()."""
    n = max(len(r) for r in rows)
    rows = [r + [''] * (n - len(r)) for r in rows]
    widths = column_widths(rows, n)
    border = ''.join('<w:%s w:val="single" w:sz="4" w:space="0" w:color="000000" />' % e
                     for e in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
    out = ['<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa" /><w:tblBorders>%s</w:tblBorders>'
           '<w:tblLayout w:type="fixed" /><w:tblCellMar><w:left w:w="57" w:type="dxa" />'
           '<w:right w:w="57" w:type="dxa" /></w:tblCellMar></w:tblPr><w:tblGrid>%s</w:tblGrid>'
           % (TEXT_W, border, ''.join('<w:gridCol w:w="%d" />' % w for w in widths))]
    for i, row in enumerate(rows):
        out.append('<w:tr>%s' % ('<w:trPr><w:tblHeader /></w:trPr>' if i == 0 else
                                 '<w:trPr><w:cantSplit /></w:trPr>'))
        for w, cell in zip(widths, row):
            text = '**%s**' % cell if i == 0 and cell else cell
            out.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa" /></w:tcPr>'
                       '<w:p w14:paraId="%08X" w14:textId="%08X">%s%s</w:p></w:tc>'
                       % (w, pid, pid + 1, ppr(ind=0, jc='left', line=240), runs(text, sz=TABLE_SZ)))
            pid += 2
        out.append('</w:tr>')
    out.append('</w:tbl>')
    return ''.join(out), pid


def add_footer(zt, parts, sect):
    """Centred PAGE field in a default footer: the part, its relationship,
    its content type, and a footerReference as the first child of sectPr."""
    parts['word/footer1.xml'] = FOOTER % ((SZ,) * 5)
    rels = parts.get('word/_rels/document.xml.rels') or zt.read('word/_rels/document.xml.rels').decode('utf8')
    if FOOTER_RID not in rels:
        rels = rels.replace('</Relationships>',
                            '<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/'
                            '2006/relationships/footer" Target="footer1.xml"/></Relationships>' % FOOTER_RID)
    parts['word/_rels/document.xml.rels'] = rels
    ct = zt.read('[Content_Types].xml').decode('utf8')
    if '/word/footer1.xml' not in ct:
        ct = ct.replace('</Types>', '<Override PartName="/word/footer1.xml" ContentType="application/'
                        'vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>')
    parts['[Content_Types].xml'] = ct
    sect = sect.replace('<w:sectPr>', '<w:sectPr><w:footerReference w:type="default" r:id="%s" />'
                        % FOOTER_RID, 1)
    return sect.replace('w:left="1134" />', 'w:left="1134" w:header="567" w:footer="567" />', 1)


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


def build(md_path, template, out_path, image_cm, page_numbers=False):
    src_dir = os.path.dirname(os.path.abspath(md_path))
    lines = io.open(md_path, encoding='utf8').read().split('\n')
    has_adviser = any(re.match(r'^\*(Научный руководитель|Scientific advis[eo]r):', raw.strip())
                      for raw in lines)
    zt = zipfile.ZipFile(template)
    rid = re.search(r'Type="[^"]*/image" Target="[^"]*" Id="([^"]+)"',
                    zt.read('word/_rels/document.xml.rels').decode('utf8'))
    rid = rid.group(1) if rid else None

    paras, adviser, figure, pid = [], [], None, 0x00100000
    front, titles, named = True, 0, False
    table = []
    for raw in lines + ['']:
        s = raw.strip()
        if s.startswith('|'):
            if not re.match(r'^\|[\s:|-]+\|$', s):
                table.append(table_cells(s))
            continue
        if table:
            xml, pid = table_xml(table, pid)
            paras.append(xml)
            table = []
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
            p = ppr(before=0 if titles == 1 else 240, ind=0, jc='center') + runs(
                '**' + s[2:] + '**', italic=True)
        elif re.match(r'^\*Рис\.', s):
            p = ppr(after=120, ind=0, jc='center') + runs(s)
        elif re.match(r'^\**(Таблица|Table) \d', s):
            p = ppr(before=120, after=60, ind=0, jc='center', keep_next=True) + runs(s)
        elif re.match(r'^\**(Источник|Source)\**:', s):
            p = ppr(before=60, after=120, ind=0, jc='left') + runs(s)
        elif front and lbl and lbl.group(1) in FRONT_LABELS:
            p = ppr(before=240, ind=0, jc='left') + runs(s)
        elif front and lbl:
            p = ppr(before=240, ind=0, jc=FRONT_JC) + runs(s, italic=True)
            if not named:
                named = lbl.group(1)
                if has_adviser:
                    p += FNREF
        elif front and s.startswith('*') and s.endswith('*'):
            p = ppr(ind=0, jc=FRONT_JC) + runs(s)
        elif LIST_HEADING.match(s):
            p = ppr(before=240, ind=0, jc='center') + runs('**' + s[3:] + '**')
        elif s.startswith('### '):
            p = ppr(before=120, ind=0, jc='left') + runs(
                '**' + s[4:] + '**', italic=True)
        elif s.startswith('## '):
            p = ppr(before=240, after=120, ind=0, jc='left') + runs(
                '**' + s[3:] + '**', italic=True)
        elif s.startswith('- '):
            p = ppr(ind=INDENT) + runs('– ' + s[2:])
        else:
            p = ppr(ind=INDENT) + runs(s)
        paras.append('<w:p w14:paraId="%08X" w14:textId="%08X">%s</w:p>' % (pid, pid + 1, p))
        pid += 2
    adviser = adviser or None

    head = re.match(r'^(.*?<w:body>)', zt.read('word/document.xml').decode('utf8'), re.S).group(1)
    parts = {}
    sect = add_footer(zt, parts, SECT) if page_numbers else SECT
    doc = head + ''.join(paras) + sect + '</w:body></w:document>'

    core = None
    if named and 'docProps/core.xml' in zt.namelist():
        core = zt.read('docProps/core.xml').decode('utf8')
        for tag in ('dc:creator', 'cp:lastModifiedBy'):
            core = re.sub(r'<%s>[^<]*</%s>' % (tag, tag), '<%s>%s</%s>' % (tag, esc(named), tag), core)

    fn = zt.read('word/footnotes.xml').decode('utf8') if 'word/footnotes.xml' in zt.namelist() else None
    if fn and adviser:
        fn = re.sub(r'<w:footnote w:id="1">.*?</w:footnote>',
                    lambda m: footnote_xml(adviser), fn, flags=re.S)

    st = zt.read('word/styles.xml').decode('utf8')
    if 'w:styleId="FootnoteReference"' not in st:
        st = st.replace('</w:styles>', FNSTYLE + '</w:styles>')
    else:
        st = None

    parts['word/document.xml'] = doc
    if figure:
        parts['media/image.png'] = open(figure, 'rb').read()
    for name, text in (('word/footnotes.xml', fn), ('word/styles.xml', st),
                       ('docProps/core.xml', core)):
        if text:
            parts[name] = text
    docxpkg.write(template, out_path, parts)
    return len(paras), adviser, figure


def check(md_path, out_path):
    import difflib
    got = ['IMG' if '<w:drawing' in p else t
           for t, p in docxpkg.paragraphs(docxpkg.part(out_path))]

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
        if s.startswith('|'):
            if not re.match(r'^\|[\s:|-]+\|$', s):
                tgt.extend(clean(c) for c in table_cells(s))
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
    ap.add_argument('--pt', type=float, default=14, help='body font size, pt (default 14)')
    ap.add_argument('--line', type=float, default=1.5, help='line spacing multiple (default 1.5)')
    ap.add_argument('--table-pt', type=float, help='table font size, pt (default: --pt)')
    ap.add_argument('--front-align', choices=('left', 'right'), default='left',
                    help='alignment of author lines before the first heading')
    ap.add_argument('--page-numbers', action='store_true', help='centred page number in the footer')
    ap.add_argument('--mark-fill', metavar='RRGGBB',
                    help='shade ==marked== text with this fill instead of yellow highlight')
    a = ap.parse_args()
    if not a.template:
        ap.error('--template (or $DOCX_TEMPLATE) is required')
    global SZ, TABLE_SZ, LINE, FRONT_JC, MARK_FILL
    MARK_FILL = a.mark_fill
    SZ, LINE, FRONT_JC = int(round(a.pt * 2)), int(round(a.line * 240)), a.front_align
    TABLE_SZ = int(round((a.table_pt or a.pt) * 2))
    out = a.out or os.path.splitext(a.markdown)[0] + '.docx'
    n, adviser, figure = build(a.markdown, a.template, out, a.image_cm, a.page_numbers)
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

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from html_md import html_to_md, tables_to_md  # noqa: E402


class Lists(unittest.TestCase):
    def test_sublist_beside_li_is_kept_and_indented(self):
        # LibreOffice's HTML export: the sublist sits in the parent <ol>,
        # not inside an <li>.
        html = ('<body><ol><li><p>Structure:</p></li>'
                '<ol><li><p>Title</p></li><li><p>Abstract</p></li></ol>'
                '</ol></body>')
        self.assertEqual(html_to_md(html),
                         '1. Structure:\n\n   1. Title\n\n   2. Abstract')

    def test_start_attribute_continues_numbering(self):
        html = '<body><ol start="3"><li>Third</li><li>Fourth</li></ol></body>'
        self.assertEqual(html_to_md(html), '3. Third\n\n4. Fourth')

    def test_sublist_inside_li_follows_its_item(self):
        html = '<body><ul><li>A<ul><li>a1</li></ul></li><li>B</li></ul></body>'
        self.assertEqual(html_to_md(html), '- A\n\n   - a1\n\n- B')

    def test_unclosed_items_stay_separate(self):
        html = '<body><ol><li><p>one</p><li><p>two</p><li><p>three</p></ol></body>'
        self.assertEqual(html_to_md(html), '1. one\n\n2. two\n\n3. three')


class Paragraphs(unittest.TestCase):
    def test_paragraph_holding_blocks_is_split(self):
        html = '<body><p><p>Samples.</p><ol><li>One</li></ol><p>Rules</p></p></body>'
        self.assertEqual(html_to_md(html), 'Samples.\n\n1. One\n\nRules')


class TablesInsideMarkdown(unittest.TestCase):
    def test_only_the_table_is_converted(self):
        md = ('## Заголовок\n\nПервый абзац.\n\n'
              '<table><tr><th>a</th><th>b</th></tr>'
              '<tr><td>1</td><td>2</td></tr></table>\n\nВторой абзац.')
        self.assertEqual(tables_to_md(md),
                         '## Заголовок\n\nПервый абзац.\n\n'
                         '| a | b |\n| --- | --- |\n| 1 | 2 |\n\nВторой абзац.')

    def test_text_without_tables_is_untouched(self):
        md = '- пункт\n  продолжение\n\nконец'
        self.assertEqual(tables_to_md(md), md)


if __name__ == '__main__':
    unittest.main()

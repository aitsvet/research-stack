import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import resolve_dois  # noqa: E402
from textfold import fold, words  # noqa: E402


class Fold(unittest.TestCase):
    def test_keeps_cyrillic_and_strips_diacritics(self):
        self.assertEqual(words('Исламские финансы: Ёлкин, Bölte'),
                         ['исламские', 'финансы', 'елкин', 'bolte'])
        self.assertEqual(fold('Cortés'), 'cortes')

    def test_russian_reference_matches_by_title_and_author(self):
        item = {'title': ['Исламские финансы в России'],
                'author': [{'family': 'Беккин'}],
                'issued': {'date-parts': [[2010]]}}
        other = {'title': ['Corporate finance'], 'author': [{'family': 'Smith'}],
                 'issued': {'date-parts': [[2010]]}}
        score, overlap, amatch, yr, title, it = resolve_dois.best(
            [other, item], 'Исламские финансы в России', 'Беккин', 2010)
        self.assertIs(it, item)
        self.assertEqual(overlap, 1.0)
        self.assertTrue(amatch)

    def test_russian_author_gate_rejects_other_author(self):
        item = {'title': ['Исламские финансы'], 'author': [{'family': 'Иванов'}]}
        _, _, amatch, *_ = resolve_dois.best([item], 'Исламские финансы', 'Беккин', None)
        self.assertFalse(amatch)


if __name__ == '__main__':
    unittest.main()

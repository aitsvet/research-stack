"""Case/diacritic folding and word tokens shared by the reference matchers.

Cyrillic stays in: a fold that keeps only [a-z0-9] turns a Russian title or
surname into an empty string, and no Russian reference can ever match.
"""
import re
import unicodedata

_WORD = re.compile(r"[a-zа-я0-9]+")


def fold(s):
    """Lowercase and strip diacritics (Bölte -> bolte, Ё -> е, й -> и)."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def words(s):
    """Folded Latin/Cyrillic/digit word tokens, in order."""
    return _WORD.findall(fold(s))

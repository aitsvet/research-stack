#!/usr/bin/env python3
"""Mark what changed in a Markdown master since a given git revision.

An editor who already has the previous export wants to see the delta, not to
re-read the paper. This writes a copy of the Markdown in which every word that
changed since REV is wrapped in `==...==`; md_docx.py renders those spans on a
yellow background, so the exported .docx/.pdf shows the edits in place.

    mark_changes.py PAPER.md --since 7618d16 [--out PAPER_marked.md] [--gap 2]

The copy is a build input, not a second master: regenerate it, export it, and
let it go. Only the original PAPER.md is ever edited.

  * Lines carrying markdown emphasis (`*`) or a heading marker are left alone.
    A `==` span that straddled a `**bold**` pair would break the run splitter,
    and highlighting a title says nothing an editor can act on.
  * --gap bridges short unmarked stretches between two marked ones, so a single
    edit reads as one highlight instead of a dotted line of fragments.
"""
import argparse
import difflib
import io
import os
import re
import subprocess
import sys


def nonempty(text):
    """Lines with their indices, blank lines dropped."""
    all_lines = text.split('\n')
    idx = [i for i, l in enumerate(all_lines) if l.strip()]
    return all_lines, idx, [all_lines[i] for i in idx]


def skip(line):
    return '*' in line or line.startswith('#')


def mark_words(old, new, gap):
    wa, wb = old.split(' '), new.split(' ')
    out = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(
            None, wa, wb, autojunk=False).get_opcodes():
        out.extend((w, tag != 'equal') for w in wb[j1:j2])

    i = 0
    while i < len(out):
        if not out[i][1]:
            i += 1
            continue
        j = i + 1
        while j < len(out) and out[j][1]:
            j += 1
        k = j
        while k < len(out) and not out[k][1] and k - j < gap:
            k += 1
        if k < len(out) and k > j and out[k][1]:
            for t in range(j, k):
                out[t] = (out[t][0], True)
            continue
        i = j

    res, i = [], 0
    while i < len(out):
        if not out[i][1]:
            res.append(out[i][0])
            i += 1
            continue
        j = i
        while j < len(out) and out[j][1]:
            j += 1
        span = ' '.join(w for w, _ in out[i:j])
        res.append('==%s==' % span if span.strip() else span)
        i = j
    return ' '.join(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('md')
    ap.add_argument('--since', required=True, help='git revision to compare against')
    ap.add_argument('--out')
    ap.add_argument('--gap', type=int, default=2,
                    help='unmarked words bridged between two marked spans')
    a = ap.parse_args()

    md = os.path.abspath(a.md)
    repo = subprocess.check_output(
        ['git', '-C', os.path.dirname(md), 'rev-parse', '--show-toplevel']).decode().strip()
    rel = os.path.relpath(md, repo)
    old_text = subprocess.check_output(
        ['git', '-C', repo, 'show', '%s:%s' % (a.since, rel)]).decode('utf8')
    new_text = io.open(md, encoding='utf8').read()

    _o_all, _o_idx, o = nonempty(old_text)
    n_all, n_idx, n = nonempty(new_text)
    result = list(n_all)

    touched = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, o, n, autojunk=False).get_opcodes():
        if tag in ('equal', 'delete'):
            continue
        pairs = min(i2 - i1, j2 - j1) if tag == 'replace' else 0
        for k in range(j2 - j1):
            line = n[j1 + k]
            if skip(line):
                continue
            result[n_idx[j1 + k]] = (mark_words(o[i1 + k], line, a.gap)
                                     if k < pairs else '==%s==' % line)
            touched += 1

    s = '\n'.join(result)
    bad = [l for l in s.split('\n') if re.search(r'==[^=]*\*[^=]*==', l)]
    if bad:
        sys.exit('a marker crossed a markdown emphasis pair: %s' % bad[0][:120])

    out = a.out or os.path.join(os.path.dirname(md),
                                '_%s_marked.md' % os.path.splitext(os.path.basename(md))[0])
    io.open(out, 'w', encoding='utf8').write(s)
    print('%s: %d lines touched, %d highlighted spans'
          % (out, touched, len(re.findall(r'==[^=]+==', s))))


if __name__ == '__main__':
    main()

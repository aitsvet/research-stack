#!/usr/bin/env python3
"""Audit a corpus directory against the Zotero library it mirrors.

The corpus is an LLM-friendly mirror of Zotero (see EXTRACT.md): every source
in it has a Zotero record, and every record that points into the corpus must
find its file there. This script reads the Zotero SQLite database read-only
and reports every discrepancy, in both directions:

  1. pointer → missing   record's `extra` names a corpus path that is absent
  2. orphan key          corpus file named by a Zotero key that does not exist
                         (or sits in the trash)
  3. unmirrored files    source files at any depth that no record covers: not
                         pointed to (nor a parent folder, nor a same-stem
                         sibling such as x.pdf for x.md) and not named by a live
                         key; reported grouped by folder
  4. collection gaps     records of a collection that the corpus mirrors mostly
                         (share >= --pool-below) but that have no file there
     discovery pools     collections mirrored below that share are candidate
                         pools by design and are only counted

Usage:
    mirror_audit.py <corpus-dir> [--db PATH] [--name NAME] [--out report.md]

  --db    Zotero database (default: $ZOTERO_SQLITE or config/Zotero/zotero.sqlite
          beside this repo). Opened immutable, so a running Zotero is fine.
  --name  how records spell the corpus in `extra` (default: the folder name).
  --pool-below  share under which a collection counts as a discovery pool (0.5).
  --skip  extra glob of non-source files (repeatable). Always skipped: README.md,
          *.json, *.tsv, topic_*.md, abstracts*.md, coverage.md, summary.md,
          dotfiles, and root-level files.

Exit status 1 when any discrepancy is found, so it can gate a commit.
"""
import argparse, os, re, sqlite3, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
# Zotero keys: 8 chars from an alphabet without 0, 1 and O
KEY = re.compile(r'^([2-9A-NP-Z]{8})(?:_|$)')


def corpus_files(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != '.git']
        for f in fn:
            out.append(os.path.relpath(os.path.join(dp, f), root))
    return out


def file_key(rel):
    stem = os.path.splitext(os.path.basename(rel))[0]
    m = KEY.match(stem)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('corpus')
    ap.add_argument('--db', default=os.environ.get('ZOTERO_SQLITE',
                    os.path.join(HERE, '..', 'config', 'Zotero', 'zotero.sqlite')))
    ap.add_argument('--name')
    ap.add_argument('--out')
    ap.add_argument('--pool-below', type=float, default=0.5)
    ap.add_argument('--skip', action='append', default=[])
    a = ap.parse_args()
    root = os.path.abspath(a.corpus)
    name = a.name or os.path.basename(root.rstrip('/'))

    db = sqlite3.connect(f'file:{os.path.abspath(a.db)}?mode=ro&immutable=1', uri=True)
    live = {}      # key -> (itemID, title)
    trashed = set()
    for iid, key, typ, title, gone in db.execute("""
        select i.itemID, i.key, t.typeName,
               (select v.value from itemData d join itemDataValues v using(valueID)
                join fields f using(fieldID) where d.itemID=i.itemID and f.fieldName='title'),
               exists(select 1 from deletedItems x where x.itemID=i.itemID)
        from items i join itemTypes t using(itemTypeID)"""):
        if typ in ('attachment', 'note', 'annotation'):
            continue
        (trashed.add(key) if gone else live.__setitem__(key, (iid, title or '')))

    # pointers: "<name>/<path>" anywhere in `extra`, path runs to end of line
    # and stops before a " — " comment (a plain hyphen occurs inside file names)
    ptr_re = re.compile(re.escape(name) + r'/([^\n]+)')
    pointers = defaultdict(list)   # itemID -> [rel paths]
    for iid, extra in db.execute("""
        select d.itemID, v.value from itemData d join itemDataValues v using(valueID)
        join fields f using(fieldID) where f.fieldName='extra'"""):
        for m in ptr_re.finditer(extra):
            p = re.split(r'\s+—\s+', m.group(1).strip())[0].strip().rstrip('/')
            pointers[iid].append(p)

    files = corpus_files(root)
    fileset = set(files)
    dirset = {os.path.dirname(f) for f in files}
    dirset |= {'/'.join(d.split('/')[:i]) for d in list(dirset) for i in range(1, d.count('/') + 1)}

    by_iid = {iid: k for k, (iid, _) in live.items()}
    mirrored = set()               # itemIDs that have something in the corpus
    missing = []
    for iid, ps in pointers.items():
        if iid not in by_iid:
            continue
        for p in ps:
            if p in fileset or p in dirset:
                mirrored.add(iid)
            else:
                missing.append((by_iid[iid], live[by_iid[iid]][1], p))

    orphans = []
    for f in files:
        k = file_key(f)
        if not k:
            continue
        if k in live:
            mirrored.add(live[k][0])
        else:
            orphans.append((f, 'trash' if k in trashed else 'absent'))

    # files no record covers, at any depth
    import fnmatch
    skip = ['README.md', '*.json', '*.tsv', 'topic_*.md', 'abstracts*.md',
            'coverage.md', 'summary.md', '*.py', '*.sh', '.*'] + a.skip
    live_ptrs = {p for iid, ps in pointers.items() if iid in by_iid for p in ps
                 if p in fileset or p in dirset}
    stem = lambda f: os.path.splitext(f)[0]
    ptr_stems = {stem(p) for p in live_ptrs}
    def covered(f):
        if f in live_ptrs or stem(f) in ptr_stems:
            return True
        parts = f.split('/')
        if any('/'.join(parts[:i]) in live_ptrs for i in range(1, len(parts))):
            return True
        k = file_key(f)
        return bool(k and k in live)
    unc = defaultdict(list)
    total = defaultdict(int); bad = defaultdict(int)   # per folder, whole subtree
    for f in files:
        if '/' not in f or any(fnmatch.fnmatch(os.path.basename(f), g) for g in skip):
            continue
        ok = covered(f)
        parts = f.split('/')[:-1]
        for i in range(1, len(parts) + 1):
            d = '/'.join(parts[:i]); total[d] += 1; bad[d] += (not ok)
        if not ok:
            unc[os.path.dirname(f)].append(os.path.basename(f))
    # collapse: a folder whose whole subtree is uncovered is one line, shown at
    # its topmost such folder; partly covered folders list their own files
    full = {d for d in total if bad[d] == total[d]}
    top_full = {d for d in full if not any(d.startswith(x + '/') for x in full)}
    unmirrored = sorted(top_full | {d for d in unc if d not in full})

    # collections the corpus mirrors at least in part
    coll = defaultdict(set); cname = {}
    for cid, ckey, cn, iid in db.execute("""
        select c.collectionID, c.key, c.collectionName, ci.itemID
        from collections c join collectionItems ci using(collectionID)"""):
        if iid in by_iid:
            coll[(cid, ckey)].add(iid); cname[(cid, ckey)] = cn
    gaps = []
    for ck, iids in coll.items():
        have = iids & mirrored
        if have and have != iids:
            gaps.append((cname[ck], ck[1], len(iids), len(have),
                         sorted((by_iid[i], live[by_iid[i]][1]) for i in iids - have)))
    gaps.sort(key=lambda g: -(g[2] - g[3]))
    pools = [g for g in gaps if g[3] / g[2] < a.pool_below]
    gaps = [g for g in gaps if g[3] / g[2] >= a.pool_below]

    L = [f'# Mirror audit: `{name}` ↔ Zotero', '',
         f'Records: {len(live)} live, {len(trashed)} in trash. Corpus files: {len(files)}. '
         f'Records mirrored in the corpus: {len(mirrored)}.', '',
         f'## 1. Pointer → missing file ({len(missing)})', '']
    L += [f'- `{k}` {t[:70]} → `{p}`' for k, t, p in sorted(missing, key=lambda x: x[2])] or ['none']
    L += ['', f'## 2. Corpus files named by a key without a live record ({len(orphans)})', '']
    L += [f'- `{f}` ({why})' for f, why in sorted(orphans)] or ['none']
    n_unc = sum(len(v) for v in unc.values())
    L += ['', f'## 3. Source files no record covers ({n_unc}; {len(unmirrored)} places)', '']
    for d in unmirrored:
        if d in top_full:
            L.append(f'- `{d}/` — whole folder, {total[d]} files')
            continue
        names = sorted({stem(n) for n in unc[d]})
        L.append(f'- `{d}/` — {len(unc[d])} files: ' + ', '.join(f'`{n}`' for n in names[:6])
                 + (f', … +{len(names) - 6}' if len(names) > 6 else ''))
    if not unmirrored:
        L.append('none')
    L += ['', f'## 4. Collections mirrored in part ({len(gaps)})', '']
    if not gaps:
        L.append('none')
    for n, ck, tot, have, rest in gaps:
        L.append(f'- **{n}** (`{ck}`): {have}/{tot} in the corpus, {tot - have} without a file')
        L += [f'  - `{k}` {t[:80]}' for k, t in rest[:15]]
        if len(rest) > 15:
            L.append(f'  - … and {len(rest) - 15} more')
    L += ['', f'## Discovery pools, not expected in full ({len(pools)})', '']
    L += [f'- {n} (`{ck}`): {have}/{tot}' for n, ck, tot, have, _ in pools] or ['none']
    text = '\n'.join(L) + '\n'
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(text)
    else:
        sys.stdout.write(text)
    sys.exit(1 if (missing or orphans or unmirrored or gaps) else 0)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Paragraph-level SQLite index over a text corpus: FTS5 + embedding vectors.

Built for the `literature` archive (see EXTRACT.md): every `.md` there carries
paginated text (`{N}` + 48 dashes), so a chunk keeps its page number and every
hit can be cited as `path#page`. Answers questions from the corpus alone —
BM25, cosine over embeddings, and reciprocal-rank fusion of the two.

    corpus_index.py build <corpus> [--db F] [--include GLOB] [--model NAME]
                                 [--limit N] [--batch N] [--no-vectors]
    corpus_index.py search "<query>" [--db F] [-k 8] [--mode hybrid|fts|vec] [--json]
    corpus_index.py stats [--db F]

Database default: `<corpus>/.index/corpus.sqlite` (keep it out of git).

Incremental by file: a document is re-chunked only when its sha256 changes;
`build` on an unchanged tree costs a stat pass. Vectors are optional —
`--no-vectors` gives a pure FTS index that needs no model at all.

Embeddings: `fastembed` (onnxruntime, CPU). The default model is a 384-dim
multilingual MiniLM: ~40 chunks/s on two cores, so a 150 MB corpus takes about
an hour once, then seconds per re-run. `minishlab/potion-multilingual-128M`
is ~50x faster and weaker; `intfloat/multilingual-e5-large` is stronger and
several times slower. e5 models get their `query:`/`passage:` prefixes here.

Env: CORPUS_INDEX_DB overrides the default database path.
"""
import argparse, hashlib, json, os, re, sqlite3, sys, time

PAGE_RE = re.compile(r"^\{(\d+)\}-{48}$", re.M)
DEFAULT_MODEL = "minishlab/potion-multilingual-128M"
MAX_CHUNK = 900          # characters of a chunk; paragraphs are merged up to this
MIN_CHUNK = 60           # shorter paragraphs are dropped (headers, page numbers)

SCHEMA = """
create table if not exists meta(key text primary key, value text);
create table if not exists docs(
  path text primary key, key text, title text, sha256 text,
  pages int, chars int, chunks int, model text, indexed_at text);
create table if not exists chunks(
  id integer primary key, path text, page int, seq int,
  chars int, text text);
create index if not exists chunks_path on chunks(path);
create virtual table if not exists chunks_fts using fts5(
  text, path unindexed, page unindexed, cid unindexed,
  tokenize = "unicode61 remove_diacritics 2");
create table if not exists vectors(
  cid integer primary key, dim integer, vec blob);
"""

def db_path(corpus, given):
    if given:
        return given
    env = os.environ.get("CORPUS_INDEX_DB")
    if env:
        return env
    return os.path.join(corpus, ".index", "corpus.sqlite")

def open_index(given):
    p = db_path(os.getcwd(), given)
    if not os.path.exists(p):
        sys.exit(f"no index at {p}\n  build one: corpus_index.py build <corpus>  "
                 f"(or pass --db / CORPUS_INDEX_DB)")
    return connect(p)

def connect(p, create=False):
    if create:
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    con = sqlite3.connect(p)
    con.execute("pragma journal_mode=wal")
    if create:
        con.executescript(SCHEMA)
    return con

def pages_of(text):
    """[{n, text}] from the paginated markdown; a file without marks is one page."""
    marks = list(PAGE_RE.finditer(text))
    if not marks:
        return [(0, text)]
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((int(m.group(1)), text[m.end():end]))
    return out

def chunk_page(page_no, body):
    """Paragraph chunks for one page: merge neighbours up to MAX_CHUNK chars.

    A paragraph shorter than MIN_CHUNK is a heading, a caption or a page number;
    it is not dropped but prepended to the next paragraph, so its words stay
    searchable ("Отношение Пророка к сплетням" is the heading of the chapter
    that answers it)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body)]
    chunks, cur, pending = [], "", ""
    for p in paras:
        p = re.sub(r"[ \t]+", " ", p).strip()
        if not p:
            continue
        if len(p) < MIN_CHUNK:
            pending = (pending + "\n\n" + p).strip() if pending else p
            continue
        if pending:
            p = pending + "\n\n" + p
            pending = ""
        if cur and len(cur) + len(p) + 2 > MAX_CHUNK:
            chunks.append(cur); cur = p
        else:
            cur = (cur + "\n\n" + p).strip() if cur else p
    if pending:
        cur = (cur + "\n\n" + pending).strip() if cur else pending
    if cur:
        chunks.append(cur)
    return [(page_no, i, c) for i, c in enumerate(chunks)]

def doc_row(path, root):
    text = open(path, encoding="utf-8", errors="replace").read()
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rel = os.path.relpath(path, root)
    key = os.path.basename(path).split("_")[0]
    if not re.fullmatch(r"[A-Z0-9]{8}", key):
        key = None
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    title = re.sub(r"^#+\s*", "", first)[:300]
    parts = []
    for n, body in pages_of(text):
        parts += chunk_page(n, body)
    return dict(path=rel, key=key, title=title, sha256=sha,
                pages=len(PAGE_RE.findall(text)), chars=len(text),
                chunks=parts, model=None)

def embedder(model):
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=model)

def prefixed(model, texts, kind):
    if "e5" in model.lower():
        p = "query: " if kind == "query" else "passage: "
        return [p + t for t in texts]
    return texts

def build(a):
    root = os.path.abspath(a.corpus)
    con = connect(db_path(root, a.db), create=True)
    cur = con.cursor()
    pats = a.include or ["*.md"]
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".index", "storage")]
        for fn in filenames:
            if not any(fn.endswith(p.lstrip("*")) for p in pats):
                continue
            if fn in ("README.md", "ZOTERO.md"):
                continue
            if a.only:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                if not rel.startswith(a.only.rstrip("/")):
                    continue
            files.append(os.path.join(dirpath, fn))
    files.sort()
    if a.limit:
        files = files[:a.limit]

    model = a.model
    cur.execute("select value from meta where key='model'")
    row = cur.fetchone()
    if row and row[0] != model and not a.no_vectors:
        print(f"  model changed {row[0]} -> {model}: dropping vectors", file=sys.stderr)
        con.execute("delete from vectors")
    cur.execute("insert or replace into meta values('model', ?)", (model,))

    emb = None if a.no_vectors else embedder(model)
    known = {r[0]: r[1] for r in con.execute("select path, sha256 from docs")}

    t0 = time.time(); n_new = n_chunks = n_vec = 0
    for i, path in enumerate(files, 1):
        d = doc_row(path, root)
        if known.get(d["path"]) == d["sha256"]:
            continue
        con.execute("delete from chunks_fts where path=?", (d["path"],))
        con.execute("delete from chunks where path=?", (d["path"],))
        con.execute("delete from docs where path=?", (d["path"],))
        ids = []
        for page, seq, text in d["chunks"]:
            cur.execute("insert into chunks(path,page,seq,chars,text) values(?,?,?,?,?)",
                        (d["path"], page, seq, len(text), text))
            cid = cur.lastrowid
            ids.append(cid)
            cur.execute("insert into chunks_fts(rowid,text,path,page,cid) values(?,?,?,?,?)",
                        (cid, text, d["path"], page, cid))
        d["model"] = model if emb else None
        cur.execute("insert into docs(path,key,title,sha256,pages,chars,chunks,model,indexed_at)"
                    " values(?,?,?,?,?,?,?,?,datetime('now'))",
                    (d["path"], d["key"], d["title"], d["sha256"], d["pages"],
                     d["chars"], len(ids), d["model"]))
        con.commit()
        n_new += 1; n_chunks += len(ids)
        if emb and ids:
            texts = prefixed(model, [c[2] for c in d["chunks"]], "passage")
            vecs = list(emb.embed(texts, batch_size=a.batch))
            cur.executemany("insert or replace into vectors(cid,dim,vec) values(?,?,?)",
                            [(cid, v.shape[0], v.astype("float32").tobytes())
                             for cid, v in zip(ids, vecs)])
            con.commit()
            n_vec += len(ids)
        el = time.time() - t0
        print(f"  [{i}/{len(files)}] {d['path']}  pages={d['pages']} chunks={len(ids)} "
              f"({el:.0f}s, {n_chunks / max(el, 1e-9):.0f} chunk/s)", flush=True)
    con.commit()
    cur.execute("select count(*), sum(chars) from docs")
    nd, nc = cur.fetchone()
    print(f"\nnew/changed docs: {n_new}, chunks written: {n_chunks}, vectors: {n_vec}; "
          f"index now {nd} docs / {nc or 0} chars")
    dbp = db_path(root, a.db)
    if n_vec and not a.no_vectors:
        write_vec_cache(con, dbp)
    con.close()

def norm(v):
    import numpy as np
    n = float((v * v).sum()) ** 0.5 or 1.0
    return v / n

def search_fts(con, q, k):
    """BM25 over the chunk text, OR-ing the query terms.

    FTS5's implicit AND is wrong for a natural-language question — "отношение
    Пророка к сплетням" matches nothing if one word is missing from the chunk —
    so the terms are OR-ed and ranked by bm25."""
    toks = re.findall(r"\w+", q, re.UNICODE)
    expr = " OR ".join('"%s"' % t for t in toks) if toks else '""'
    rows = con.execute(
        "select cid, path, page, text, bm25(chunks_fts) as s from chunks_fts "
        "where chunks_fts match ? order by bm25(chunks_fts) limit ?", (expr, k)).fetchall()
    return [(r[0], r[1], r[2], r[3], -r[4]) for r in rows]   # bm25: lower is better

def _vec_cache(con, dbp):
    """(ids, matrix) for the whole vector table, memmapped when the sidecar exists.

    Scanning 130k BLOBs through SQLite costs ~15 s per query; the sidecar pair
    `<db>.vecids.npy` / `<db>.vecmat.npy` written at the end of `build` brings a
    search down to well under a second. A stale sidecar (row count mismatch) is
    ignored and the SQLite path is used instead."""
    import numpy as np
    if not dbp:
        return None
    idp, matp = dbp + ".vecids.npy", dbp + ".vecmat.npy"
    if not (os.path.exists(idp) and os.path.exists(matp)):
        return None
    try:
        ids = np.load(idp, mmap_mode="r")
        mat = np.load(matp, mmap_mode="r")
    except Exception:
        return None
    n = con.execute("select count(*) from vectors").fetchone()[0]
    if len(ids) != n or mat.shape[0] != n:
        return None
    return ids, mat

def write_vec_cache(con, dbp):
    import numpy as np
    rows = con.execute("select cid, vec from vectors order by cid").fetchall()
    if not rows:
        return
    dim = len(rows[0][1]) // 4
    ids = np.array([r[0] for r in rows], dtype="int64")
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype="float32").reshape(len(rows), dim)
    np.save(dbp + ".vecids.npy", ids)
    np.save(dbp + ".vecmat.npy", mat)
    print(f"  vector sidecar written: {len(rows)} x {dim}")

def search_vec(con, q, k, model, dbp=None):
    import numpy as np
    emb = embedder(model)
    qv = norm(next(iter(emb.embed(prefixed(model, [q], "query")))).astype("float32"))
    cache = _vec_cache(con, dbp)
    best = []
    if cache:
        ids, mat = cache
        block = 50000
        for start in range(0, len(ids), block):
            m = np.array(mat[start:start + block], dtype="float32")
            m /= (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
            scores = m @ qv
            idx = np.argsort(-scores)[:k]
            best += [(int(ids[start + i]), float(scores[i])) for i in idx]
    else:
        cur = con.execute("select cid, vec from vectors")
        while True:
            rows = cur.fetchmany(20000)
            if not rows:
                break
            ids = [r[0] for r in rows]
            m = np.vstack([np.frombuffer(r[1], dtype="float32") for r in rows])
            m /= (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
            scores = m @ qv
            idx = np.argsort(-scores)[:k]
            best += [(ids[i], float(scores[i])) for i in idx]
    best.sort(key=lambda x: -x[1])
    out = []
    for cid, score in best[:k]:
        r = con.execute("select path, page, text from chunks where id=?", (cid,)).fetchone()
        if r:
            out.append((cid, r[0], r[1], r[2], score))
    return out

def rrf(*lists, k=60):
    score, keep = {}, {}
    for lst in lists:
        for rank, hit in enumerate(lst):
            score[hit[0]] = score.get(hit[0], 0.0) + 1.0 / (k + rank + 1)
            keep[hit[0]] = hit
    return [keep[cid] for cid in sorted(score, key=lambda c: -score[c])]

def search(a):
    dbp = db_path(os.getcwd(), a.db)
    con = open_index(a.db)
    row = con.execute("select value from meta where key='model'").fetchone()
    model = row[0] if row else DEFAULT_MODEL
    n = a.k
    fts = vec = []
    if a.mode in ("hybrid", "fts"):
        fts = search_fts(con, a.query, max(n, 50))
    if a.mode in ("hybrid", "vec"):
        vec = search_vec(con, a.query, max(n, 50), model, dbp)
    ranked = {"fts": fts, "vec": vec, "hybrid": rrf(fts, vec)}[a.mode]
    # one chunk per page: neighbouring paragraphs of the same page crowd the list
    hits, seen = [], set()
    for h in ranked:
        if (h[1], h[2]) in seen:
            continue
        seen.add((h[1], h[2])); hits.append(h)
        if len(hits) == n:
            break
    if a.context:
        for i, (cid, path, page, text, score) in enumerate(hits, 1):
            body = re.sub(r"\s+", " ", text).strip()
            print(f"[{i}] {path}#p{page}\n{body}\n")
        return
    if a.json:
        print(json.dumps([{"path": h[1], "page": h[2], "score": round(h[4], 4),
                           "text": h[3]} for h in hits], ensure_ascii=False, indent=1))
        return
    for i, (cid, path, page, text, score) in enumerate(hits, 1):
        snippet = re.sub(r"\s+", " ", text)[:400]
        print(f"\n{i:2d}. {path}#p{page}  [{score:.3f}]\n    {snippet}")

def stats(a):
    con = open_index(a.db)
    docs, chunks, chars = con.execute(
        "select count(*), coalesce(sum(chunks),0), coalesce(sum(chars),0) from docs").fetchone()
    vecs = con.execute("select count(*), coalesce(sum(dim),0) from vectors").fetchone()
    model = con.execute("select value from meta where key='model'").fetchone()
    db = con.execute("pragma page_count").fetchone()[0] * con.execute("pragma page_size").fetchone()[0]
    print(f"docs: {docs}, chunks: {chunks}, chars: {chars:,}, "
          f"vectors: {vecs[0]} (dim {vecs[1] // max(vecs[0], 1)}), db: {db / 1e6:.1f} MB, "
          f"model: {model[0] if model else '-'}")

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="index a corpus directory (incremental)")
    b.add_argument("corpus"); b.add_argument("--db"); b.add_argument("--include", action="append")
    b.add_argument("--model", default=DEFAULT_MODEL); b.add_argument("--limit", type=int)
    b.add_argument("--only", help="index only files under this corpus-relative prefix")
    b.add_argument("--batch", type=int, default=64); b.add_argument("--no-vectors", action="store_true")
    s = sub.add_parser("search", help="query the index")
    s.add_argument("query"); s.add_argument("--db"); s.add_argument("-k", type=int, default=8)
    s.add_argument("--mode", choices=["hybrid", "fts", "vec"], default="hybrid")
    s.add_argument("--json", action="store_true")
    s.add_argument("--context", action="store_true",
                   help="print one prompt-ready block of the hits with citations")
    st = sub.add_parser("stats"); st.add_argument("--db")
    a = ap.parse_args(argv)
    {"build": build, "search": search, "stats": stats}[a.cmd](a)

if __name__ == "__main__":
    main(sys.argv[1:])

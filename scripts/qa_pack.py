#!/usr/bin/env python3
"""Bundle a corpus (texts + tools + agent instructions) into one transferable QA pack.

The pack is what a *different* machine needs to answer from a corpus: the
paginated markdown sources, the two scripts that read and query them, a pinned
environment, and instructions written for an opencode agent (SKILL.md) and a
human (README.md).

    qa_pack.py --corpus <dir> --prefix <subdir> --primary <ITEM_KEY>
               --out ~/qa-pack.tar.gz [--title "…"] [--no-index]

**The index is built on the receiving host, not shipped.** `setup.sh` creates the
venv, installs the pinned deps, builds a baseline index from the bundled texts
and runs a self-test; README/SKILL explain how to rebuild it with a stronger
embedder, which is the only thing that decides answer precision. Shipping a
110 MB blobs file instead would freeze one model and one machine's throughput
into the transfer.

`--no-index` (the mode this was written for) omits the database even if one
exists locally; without it an existing `<corpus>/.index/*.sqlite` is copied for
convenience, together with its `.vecids.npy` / `.vecmat.npy` sidecar.

`--bundle-model` copies the fastembed weights into `models/` (one copy, blob
symlinks preserved) for a host with no internet; by default the weights are
stock and download on first build.
"""
import argparse, datetime, hashlib, json, os, shutil, sys, tarfile, tempfile

README = """# {title} — QA pack

Answer questions from **{title}** only, with the other titles in the set as
semantic support. The pack ships text, not vectors: `./setup.sh` builds the
search index on this machine, and you choose how strong the embedder is.

| | |
|---|---|
| primary title | `{primary}` — {title} |
| sources | `texts/` — {docs} files, {chars:,} chars, paginated (`{N}` + 48 dashes per page) |
| chunks (baseline) | ≈ {chunks:,} paragraphs |
| index | `index/books.sqlite`, built by `setup.sh` — **not shipped** |
| build date | {created} |

## Setup (does everything)

```bash
./setup.sh
```

It creates `.venv`, installs the pinned requirements (from `wheels/` if present,
so it also works offline), builds the baseline index over `texts/`, prints the
index stats and runs a self-test query. Takes about a minute on a 2-core box,
most of it the stock model download (~240 MB, one time).

Requires Python 3.10+. For the screenshot loop also `tesseract-ocr` +
`tesseract-ocr-rus` and one screenshot tool: `grim` (Wayland),
`maim`/`scrot`/`gnome-screenshot` (X11) or ImageMagick `import`.

## Build the index yourself (and make it precise)

The baseline index uses a small, fast embedder so setup finishes quickly. Answer
precision comes from what you embed with, so build the index again with a
stronger model — the script is incremental and only re-embeds what changed:

```bash
# baseline, seconds per book (what setup.sh runs)
./.venv/bin/python corpus_index.py build . --only texts --db index/books.sqlite \\
    --model minishlab/potion-multilingual-128M

# stronger: 768-dim multilingual mpnet — hours on a 2-core box, minutes on 16 cores
./.venv/bin/python corpus_index.py build . --only texts --db index/books.sqlite \\
    --model sentence-transformers/paraphrase-multilingual-mpnet-base-v2

# strongest listed here: 1024-dim e5-large, needs ~3 GB RAM for the model
./.venv/bin/python corpus_index.py build . --only texts --db index/books.sqlite \\
    --model intfloat/multilingual-e5-large

# no model at all: pure FTS5/BM25 index
./.venv/bin/python corpus_index.py build . --only texts --db index/books.sqlite --no-vectors
```

Measured on a 2-core CPU with real chunks, so you can budget the build:

| model | dim | throughput | 7 000 chunks |
|---|---|---|---|
| `minishlab/potion-multilingual-128M` | 256 | 500–800 chunk/s | ~15 s |
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | ~13 chunk/s | ~9 min |
| `paraphrase-multilingual-mpnet-base-v2` | 768 | ~1.2 chunk/s | ~1.6 h |
| `intfloat/multilingual-e5-large` | 1024 | ~0.2 chunk/s | ~10 h |

Two ways to buy precision without waiting for a big build:

1. **Rerank per query** — keep the fast index, re-score the top candidates with a
   stronger model (seconds to tens of seconds per question, no re-indexing):
   ```bash
   ./.venv/bin/python corpus_index.py search "вопрос" --db index/books.sqlite \\
       --primary {primary} -k 3 \\
       --rerank sentence-transformers/paraphrase-multilingual-mpnet-base-v2
   ```
2. **Build on a bigger machine** — the index is a single SQLite file; build it
   where the CPU/GPU is, copy `index/` over, query anywhere.

`--model` on `build` and `--rerank` on `search` must not be mixed up: the index
stores one model's vectors, the reranker re-embeds only the candidates it sees.

## Ask

```bash
./.venv/bin/python corpus_index.py search "вопрос" \\
    --db index/books.sqlite --primary {primary} -k 3 --context
```

- `--primary {primary}` — hits from the chosen title rank first; the other titles
  fill at most `--support` slots (default 2). If nothing from the primary
  matches, the run says so on stderr instead of silently answering from a
  different book.
- Every hit prints as `path#page` — cite that anchor.
- `--mode fts` (lexical only), `--mode vec` (semantic only), `--json`.

## Screenshot → answer (auto mode)

```bash
./.venv/bin/python shot_ocr.py ask --region 100,200,900,400 \\
    --db index/books.sqlite --primary {primary} -k 3
```

Captures the region, OCRs it with **tesseract** (`--ocr tesseract`, the default:
~1 s, local, exact on on-screen text), queries the index and prints the OCR text
plus the cited chunks in one block. `--image shot.png` skips the capture. For a
photographed page or Arabic script, `--ocr chandra` with `CHANDRA_URL` set uses a
VLM instead — better layout handling, but it can substitute a look-alike word,
and here the OCR text *is* the query, so a substitution silently changes the
question.
"""

SKILL = """---
name: corpus-qa
description: Answer questions from the corpus in this directory — the base title is {primary} ({title}); the other titles are support only. Build/query the SQLite index with corpus_index.py, and use shot_ocr.py (tesseract) when the question arrives as a screenshot of a screen area.
---

# Corpus QA

Everything runs from this directory. `setup.sh` has already built
`.venv` and the baseline index; if `index/books.sqlite` is missing, build it
before answering anything:

```bash
./.venv/bin/python corpus_index.py build . --only texts --db index/books.sqlite \\
    --model minishlab/potion-multilingual-128M
```

For sharper answers add `--rerank sentence-transformers/paraphrase-multilingual-mpnet-base-v2`
to `search` (seconds per query), or rebuild the index with a stronger `--model`
when the machine can afford it. Never answer from an index whose model is not
the one recorded in `corpus_index.py stats --db index/books.sqlite`.

## Rules

1. **Answer from `{primary}` ({title}).** Other titles may be appended as
   support; never let them carry the answer alone. Keep `--primary {primary}` on
   every search — the retriever enforces the order, and warns when the base
   title has no matching chunk.
2. **Retrieve before answering, always.**
   `./.venv/bin/python corpus_index.py search "<question>" --db index/books.sqlite --primary {primary} -k 3 --context`
3. **Cite `path#page`** for every claim — `{title}, с. N`, taking N from the hit
   itself (`#pN`). Check the book's front matter before assuming it equals the
   printed folio.
4. **If the chunks do not contain the answer, say so** and offer the closest
   passages. Never complete from memory, never invent page numbers.
5. Keep the answer short: the chunk that answers, its citation, one line of
   context. Quote verbatim when the wording matters; mark elisions with `[…]`.

## Screenshot of a screen area

```bash
./.venv/bin/python shot_ocr.py ask --region X,Y,W,H \\
    --db index/books.sqlite --primary {primary} -k 3
```

Tesseract is the engine here (default, ~1 s). If the region is a photo of a page
or Arabic script and tesseract garbles it, `--ocr chandra` with `CHANDRA_URL` set
is the fallback — but verify its reading against the chunk it retrieves, because
a substituted word changes the query itself.
"""

SETUP = """#!/usr/bin/env bash
# Prepare the QA pack: venv, pinned deps, baseline index, self-test.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
$PY -m venv .venv
./.venv/bin/pip install -q --upgrade pip wheel

if [ -d wheels ]; then
  echo "installing from ./wheels (offline)"
  ./.venv/bin/pip install -q --no-index --find-links wheels -r requirements.txt
else
  ./.venv/bin/pip install -q -r requirements.txt
fi

export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-$PWD/models}"
mkdir -p "$FASTEMBED_CACHE_PATH"

MODEL=${MODEL:-minishlab/potion-multilingual-128M}
DB=index/books.sqlite
mkdir -p index
if [ -f "$DB" ]; then
  echo "index already present: $DB"
else
  echo "--- building the baseline index with $MODEL"
  ./.venv/bin/python corpus_index.py build . --only texts --db "$DB" --model "$MODEL"
fi

echo "--- index"
./.venv/bin/python corpus_index.py stats --db "$DB"

echo "--- self-test query"
./.venv/bin/python corpus_index.py search "отношение к злословию" \\
    --db "$DB" --primary {PRIMARY} -k 1 --context | head -12

cat <<'NOTE'

Baseline built. For sharper answers either rerank per query:

    ./.venv/bin/python corpus_index.py search "вопрос" --db index/books.sqlite \\
        --primary {PRIMARY} -k 3 \\
        --rerank sentence-transformers/paraphrase-multilingual-mpnet-base-v2

or rebuild the index with a stronger model (incremental, only changed files):

    ./.venv/bin/python corpus_index.py build . --only texts --db index/books.sqlite \\
        --model sentence-transformers/paraphrase-multilingual-mpnet-base-v2

Keep FASTEMBED_CACHE_PATH exported (set to ./models here) so the model is reused.
NOTE
"""

REQS = """fastembed==0.9.1
numpy>=1.26,<3
Pillow>=10
"""

def fill(tpl, fmt):
    """Substitute {name} placeholders literally.

    str.format cannot be used here: the templates contain SQL, regexes and shell
    braces that are not placeholders (`{N}`, `{0,}`, `${PYTHON:-python3}`)."""
    for k, v in fmt.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def bundle_model(cache_root, model_name, dst_models):
    """Copy one fastembed model out of its shared cache without duplicating weights.

    A cache entry is `models--X/{blobs,snapshots,…}` where `snapshots/<rev>/model.onnx`
    links to `models--X/blobs/<sha>`, which links again to `<cache>/blobs/<xx>/<sha>`.
    The pack mirrors that layout (`models/blobs/…` + `models/models--X/…`) and pulls in
    only the blobs this entry points at, so a 235 MB file stays one file.
    """
    src_model = os.path.join(cache_root, model_name)
    dst_model = os.path.join(dst_models, model_name)
    shutil.copytree(src_model, dst_model, symlinks=True, dirs_exist_ok=True)
    needed = {}
    for root, _, files in os.walk(src_model):
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p):
                real = os.path.realpath(p)
                if real.startswith(cache_root + os.sep) and os.path.isfile(real):
                    needed[os.path.relpath(real, cache_root)] = real
    for rel, real in needed.items():
        dst = os.path.join(dst_models, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            shutil.copyfile(real, dst)
    return dst_model

def count_chunks(pack, texts):
    """Docs/chunks/chars as the builder would produce them (no embeddings), plus
    the page-mark count of each file so the instruction can state the expected
    completeness check."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import corpus_index as ci
    docs = chunks = chars = 0
    pages = {}
    for t in texts:
        d = ci.doc_row(os.path.join(pack, "texts", t), pack)
        docs += 1; chunks += len(d["chunks"]); chars += d["chars"]
        pages[t.split("_")[0]] = d["pages"]
    return docs, chunks, chars, pages

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--prefix", required=True, help="corpus subdirectory to include")
    ap.add_argument("--primary", required=True, help="item key of the base title")
    ap.add_argument("--db", default=None, help="index to copy (omitted with --no-index)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default="~/qa-pack.tar.gz")
    ap.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    ap.add_argument("--model-cache", default="/tmp/fastembed_cache")
    ap.add_argument("--bundle-model", action="store_true")
    ap.add_argument("--no-index", action="store_true", help="do not ship a database")
    ap.add_argument("--no-texts", action="store_true")
    ap.add_argument("--selftest-query", default="", help="query for the instruction's acceptance table")
    ap.add_argument("--selftest-page", default="", help="expected page for that query")
    ap.add_argument("--instructions-only", action="store_true",
                    help="ship texts + one INSTRUCTION.md and nothing else: the receiving "
                         "agent builds the FTS index itself from the chunking rules")
    ap.add_argument("--no-tar", action="store_true", help="leave the directory, no tarball")
    a = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(a.corpus))
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.abspath(os.path.expanduser(a.out))
    work = tempfile.mkdtemp(prefix="qa_pack_")
    pack = os.path.join(work, "iqra-qa")
    os.makedirs(os.path.join(pack, "texts"))

    texts = []
    for dirpath, _, filenames in os.walk(os.path.join(root, a.prefix)):
        for fn in sorted(filenames):
            if fn.endswith(".md") and fn != "README.md":
                shutil.copyfile(os.path.join(dirpath, fn), os.path.join(pack, "texts", fn))
                texts.append(fn)
    if not texts:
        sys.exit(f"no markdown under {os.path.join(root, a.prefix)}")

    if not a.instructions_only:
        for fn in ("corpus_index.py", "shot_ocr.py"):
            shutil.copyfile(os.path.join(here, fn), os.path.join(pack, fn))

    docs, chunks, chars, pagecounts = count_chunks(pack, texts)
    shipped_index = False
    if not a.no_index and a.db and not a.instructions_only:
        db = os.path.abspath(os.path.expanduser(a.db))
        os.makedirs(os.path.join(pack, "index"), exist_ok=True)
        shutil.copyfile(db, os.path.join(pack, "index", "books.sqlite"))
        for ext in (".vecids.npy", ".vecmat.npy"):
            if os.path.exists(db + ext):
                shutil.copyfile(db + ext, os.path.join(pack, "index", "books.sqlite" + ext))
        shipped_index = True

    model_dir = None
    if a.bundle_model and not a.instructions_only:
        leaf = a.model.rsplit("/", 1)[-1].lower()
        cand = os.path.join(a.model_cache, "models--" + a.model.replace("/", "--"))
        if not os.path.isdir(cand) and os.path.isdir(a.model_cache):
            hits = [d for d in os.listdir(a.model_cache) if leaf in d.lower()]
            cand = os.path.join(a.model_cache, hits[0]) if hits else None
        if not cand or not os.path.isdir(cand):
            sys.exit(f"model cache for {a.model} not found under {a.model_cache}")
        model_dir = bundle_model(a.model_cache, os.path.basename(cand),
                                 os.path.join(pack, "models"))
        print("model bundled:", model_dir)

    title = a.title or next((open(os.path.join(pack, "texts", t), encoding="utf-8")
                             .readline().strip() for t in texts if t.startswith(a.primary)),
                            a.primary)
    title = title.lstrip("# ").strip() or a.primary
    created = datetime.date.today().isoformat()
    fmt = dict(title=title, primary=a.primary, docs=docs, chunks=f"{chunks:,}", chars=f"{chars:,}",
               model=a.model, created=created,
               selftest_query=a.selftest_query or "the book's opening phrase",
               selftest_page=a.selftest_page or "N",
               pagecounts=", ".join(f"`{k}` {v}" for k, v in sorted(pagecounts.items())))
    if a.instructions_only:
        tpl = open(os.path.join(here, "qa_pack_instruction.md"), encoding="utf-8").read()
        open(os.path.join(pack, "INSTRUCTION.md"), "w").write(fill(tpl, fmt))
    else:
        open(os.path.join(pack, "README.md"), "w").write(fill(README, fmt))
        open(os.path.join(pack, "SKILL.md"), "w").write(fill(SKILL, fmt))
        open(os.path.join(pack, "setup.sh"), "w").write(
            fill(SETUP, {"primary": a.primary, "PRIMARY": a.primary}))
        open(os.path.join(pack, "requirements.txt"), "w").write(REQS)
        os.chmod(os.path.join(pack, "setup.sh"), 0o755)

    manifest = dict(created=created, primary=a.primary, title=title, corpus=root,
                    texts=texts, index_shipped=shipped_index,
                    expected=dict(docs=docs, chunks=chunks, chars=chars),
                    model_for_build=fmt["model"], model_bundled=bool(model_dir),
                    files={})
    for dirpath, _, filenames in os.walk(pack):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, pack)
            if os.path.islink(p):
                manifest["files"][rel] = dict(symlink=os.readlink(p))
                continue
            manifest["files"][rel] = dict(size=os.path.getsize(p), sha256=sha256(p))
    open(os.path.join(pack, "MANIFEST.json"), "w").write(
        json.dumps(manifest, ensure_ascii=False, indent=1))

    if a.no_tar:
        print("pack directory:", pack)
        return
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        tar.add(pack, arcname="iqra-qa")
    print(f"written {out} ({os.path.getsize(out) / 1e6:.0f} MB), "
          f"{len(manifest['files'])} files, index shipped: {shipped_index}")

if __name__ == "__main__":
    main(sys.argv[1:])

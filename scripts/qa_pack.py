#!/usr/bin/env python3
"""Bundle a corpus index + its texts + agent instructions into one transferable pack.

The pack is what a *different* machine needs to answer from a corpus offline:
the SQLite index (FTS5 + vectors), the markdown sources it was built from, the
two scripts that use them, a pinned environment, and instructions written for an
opencode agent (SKILL.md) and a human (README.md).

    qa_pack.py --corpus <corpus-dir> --db <corpus>/.index/<name>.sqlite
               --prefix <subdir> --primary <ITEM_KEY> --out ~/qa-pack.tar.gz
               [--bundle-model] [--no-texts] [--title "…"]

`--bundle-model` copies the fastembed model weights into `models/` (≈240 MB for
the multilingual MiniLM) so the receiving host needs no internet at query time;
without it setup.sh downloads them on first search. Weights are copied
dereferenced (fastembed stores them as symlinks into a blob store), and
`FASTEMBED_CACHE_PATH` in setup.sh points at the copy.

The index is copied as `<db>` plus its `.vecids.npy` / `.vecmat.npy` sidecar —
search reads the sidecar and skips the 15 s BLOB scan. Requirements are pinned;
if `wheels/` exists next to the pack, setup.sh installs from it (fully offline).
"""
import argparse, datetime, hashlib, json, os, shutil, subprocess, sys, tarfile, tempfile

README = """# {title} — QA pack

Answer questions from **{title}** only, with the other titles in the set as
semantic support. Everything needed is in this directory; nothing phones home
except the first model download (unless `models/` is populated).

| | |
|---|---|
| primary title | `{primary}` — {title} |
| index | `index/books.sqlite` ({docs} documents, {chunks} chunks, {chars} chars) |
| embedder | `{model}` (fastembed/ONNX, CPU) |
| sources | `texts/` — paginated markdown, `{{N}}` + 48 dashes per page |
| build | {created} |

## Setup

```bash
./setup.sh          # venv + pinned deps + self-test query
```

Requires Python 3.10+ and, for the screenshot loop, `tesseract-ocr` +
`tesseract-ocr-rus` and one screenshot tool (`grim` on Wayland,
`maim`/`scrot`/`gnome-screenshot` on X11).

## Ask

```bash
./.venv/bin/python corpus_index.py search "вопрос" \\
    --db index/books.sqlite --primary {primary} -k 3 --context
```

- `--primary {primary}` — hits from the chosen title rank first; the other
  titles fill at most `--support` slots (default 2) and exist to widen context,
  never to replace the base book.
- Every hit is printed as `path#page`; quote that anchor in the answer.
- `--mode fts` (lexical only), `--mode vec` (semantic only), `--json`.
- `--context` prints one prompt-ready block; that is what an agent should read.

## Screenshot → answer (auto mode)

```bash
./.venv/bin/python shot_ocr.py ask --region 100,200,900,400 \\
    --db index/books.sqlite --primary {primary} -k 3
```

Grabs the region, OCRs it (`--ocr tesseract` by default; `--ocr chandra` with
`CHANDRA_URL` set for photographed pages), queries the index and prints the OCR
text plus the retrieved chunks. `--image path.png` skips the capture — useful
when the picture is already a file.

## Model

| stage | model | cost here (2-core CPU) |
|---|---|---|
| index vectors | `{model}` (384 d) | one-off ≈ 14 chunks/s → ~9 min for this set |
| rerank (optional) | `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` (768 d) | ~1.2 chunks/s → +10–20 s per query over 10–20 candidates |
| rerank (strongest) | `intfloat/multilingual-e5-large` (1024 d) | ~0.2 chunks/s, ~3 GB RSS → minutes per query, needs a big host |

The index is built with a small fast embedder on purpose: precision at rank 1 is
bought by the optional second pass, not by embedding 7 000 chunks with a model
that runs at a tenth of the speed. Both are one flag away:

```bash
# precise answers (slow per query, no re-indexing)
./.venv/bin/python corpus_index.py search "вопрос" --db index/books.sqlite \
    --primary {primary} -k 3 --rerank sentence-transformers/paraphrase-multilingual-mpnet-base-v2

# precise index instead (hours of build, fast queries)
./.venv/bin/python corpus_index.py build . --db index/books.sqlite \
    --model intfloat/multilingual-e5-large
```

Weights are stock fastembed models: `setup.sh` downloads `{model}` on first use
into `./models` unless this pack already ships them (`MANIFEST.json` →
`model_bundled`).

## Rebuild

The index is incremental: dropping a new `KEY_name.md` into `texts/` and running

```bash
./.venv/bin/python corpus_index.py build . --db index/books.sqlite \\
    --model {model}
```

re-chunks only what changed. `--no-vectors` builds a pure FTS5 index with no
model at all.
"""

SKILL = """---
name: corpus-qa
description: Answer questions from the corpus in this directory — primary title {primary} ({title}), other titles only as support. Use the bundled SQLite index (FTS5 + vectors) through corpus_index.py, and shot_ocr.py when the question arrives as a screenshot of a screen area.
---

# Corpus QA

Everything runs from this directory with `.venv/bin/python` (see `setup.sh`).

## Rules

1. **Answer from `{primary}` ({title}).** Other titles may be appended as
   support; never let them carry the answer alone. The retriever already
   enforces the order with `--primary {primary}` — keep that flag.
2. **Retrieve before answering, always.**
   `./.venv/bin/python corpus_index.py search "<question>" --db index/books.sqlite --primary {primary} -k 3 --context`
3. **Cite `path#page`** for every claim — `{title}, с. N`, where N is the page
   taken from the hit itself (`#pN`); check the book's own front matter before
   assuming it equals the printed folio.
4. **If the chunks do not contain the answer, say so** and offer the closest
   passages. Do not complete from memory and do not invent page numbers.
5. Keep the answer short: the chunk that answers, its citation, and one line of
   context. Quote verbatim when the wording matters; mark elisions with `[…]`.

## Screenshot of a screen area

```bash
./.venv/bin/python shot_ocr.py ask --region X,Y,W,H \\
    --db index/books.sqlite --primary {primary} -k 3
```

`--ocr tesseract` (default) reads clean on-screen text in about a second;
use `--ocr chandra` with `CHANDRA_URL` set when the region is a photo or a scan —
a VLM OCR is better on layout and Arabic, but it can substitute a look-alike
word, which distorts a retrieval query. `--image shot.png` skips the capture.

## Without a screenshot

Call `search` directly with the question text; `--context` output is the whole
answer material — do not re-rank it by intuition, the fusion already did.
"""

SETUP = """#!/usr/bin/env bash
# Prepare the QA pack: venv, pinned deps, model cache, self-test.
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
echo "FASTEMBED_CACHE_PATH=$FASTEMBED_CACHE_PATH"

echo "--- index"
./.venv/bin/python corpus_index.py stats --db index/books.sqlite

echo "--- self-test query"
./.venv/bin/python corpus_index.py search "отношение Пророка к злословию" \\
    --db index/books.sqlite --primary {primary} -k 1 --context | head -12 || {
      echo "self-test failed"; exit 1; }

cat <<'NOTE'

Add to your shell so searches reuse the bundled model cache:

    export FASTEMBED_CACHE_PATH="$PWD/models"

Or run everything through ./.venv/bin/python with that variable exported.
NOTE
"""

REQS = """fastembed==0.9.1
numpy>=1.26,<3
Pillow>=10
"""

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def bundle_model(cache_root, model_name, dst_models):
    """Copy one fastembed model out of its shared cache without duplicating weights.

    A cache entry is `models--X/{blobs,snapshots,…}` where `snapshots/<rev>/model.onnx`
    links to `models--X/blobs/<sha>`, which in turn links to `<cache>/blobs/<xx>/<sha>`.
    Copying the entry with symlinks intact is 2 MB; the weights are pulled in only for
    the blobs that entry actually points at, so the pack mirrors the cache root layout
    (`models/blobs/...` + `models/models--X/...`) and one 235 MB file stays one file.
    Dereferencing instead doubles it — that was the first, wrong implementation."""
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

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--prefix", required=True, help="corpus subdirectory to include")
    ap.add_argument("--primary", required=True, help="Zotero key of the base title")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default="~/qa-pack.tar.gz")
    ap.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    ap.add_argument("--model-cache", default="/tmp/fastembed_cache")
    ap.add_argument("--bundle-model", action="store_true")
    ap.add_argument("--no-texts", action="store_true")
    ap.add_argument("--no-tar", action="store_true", help="leave the directory, no tarball")
    a = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(a.corpus))
    db = os.path.abspath(os.path.expanduser(a.db))
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.abspath(os.path.expanduser(a.out))
    work = tempfile.mkdtemp(prefix="qa_pack_")
    pack = os.path.join(work, "iqra-qa")
    os.makedirs(os.path.join(pack, "index"))
    os.makedirs(os.path.join(pack, "texts"))

    stats = subprocess.run([sys.executable, os.path.join(here, "corpus_index.py"),
                            "stats", "--db", db], capture_output=True, text=True)
    if stats.returncode != 0:
        sys.exit("cannot read the index: " + stats.stderr.strip()[:300])
    info = stats.stdout.strip()
    docs = int(info.split("docs:")[1].split(",")[0])
    chunks = int(info.split("chunks:")[1].split(",")[0])
    chars = int(info.split("chars:")[1].split(",")[0])
    print("index:", info)

    # index + sidecar
    shutil.copyfile(db, os.path.join(pack, "index", "books.sqlite"))
    sidecar = 0
    for ext in (".vecids.npy", ".vecmat.npy"):
        if os.path.exists(db + ext):
            shutil.copyfile(db + ext, os.path.join(pack, "index", "books.sqlite" + ext))
            sidecar += 1

    # sources
    texts = []
    if not a.no_texts:
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, a.prefix)):
            for fn in sorted(filenames):
                if fn.endswith(".md") and fn != "README.md":
                    src = os.path.join(dirpath, fn)
                    shutil.copyfile(src, os.path.join(pack, "texts", fn))
                    texts.append(fn)

    # scripts
    for fn in ("corpus_index.py", "shot_ocr.py"):
        shutil.copyfile(os.path.join(here, fn), os.path.join(pack, fn))

    # model weights
    model_dir = None
    if a.bundle_model:
        leaf = a.model.rsplit("/", 1)[-1].lower()
        cand = os.path.join(a.model_cache, "models--" + a.model.replace("/", "--"))
        if not os.path.isdir(cand):
            hits = [d for d in os.listdir(a.model_cache) if leaf in d.lower()]
            cand = os.path.join(a.model_cache, hits[0]) if hits else None
        if not cand or not os.path.isdir(cand):
            sys.exit(f"model cache for {a.model} not found under {a.model_cache}")
        model_dir = bundle_model(a.model_cache, os.path.basename(cand),
                                 os.path.join(pack, "models"))
        weights = sum(os.path.getsize(os.path.join(r, f))
                      for r, _, fs in os.walk(os.path.join(pack, "models"))
                      for f in fs if not os.path.islink(os.path.join(r, f)))
        print(f"model bundled: {model_dir} ({weights / 1e6:.0f} MB of weights)")

    title = a.title or next((open(os.path.join(pack, "texts", t), encoding="utf-8").readline().strip()
                             for t in texts if t.startswith(a.primary)), a.primary)
    title = title.lstrip("# ").strip() or a.primary

    created = datetime.date.today().isoformat()
    fmt = dict(title=title, primary=a.primary, docs=docs, chunks=chunks, chars=chars,
               model=a.model, created=created)
    open(os.path.join(pack, "README.md"), "w").write(README.format(**fmt))
    open(os.path.join(pack, "SKILL.md"), "w").write(SKILL.format(**fmt))
    open(os.path.join(pack, "setup.sh"), "w").write(SETUP.replace("{primary}", a.primary))
    open(os.path.join(pack, "requirements.txt"), "w").write(REQS)
    os.chmod(os.path.join(pack, "setup.sh"), 0o755)

    manifest = dict(created=created, primary=a.primary, title=title,
                    corpus=root, index=dict(docs=docs, chunks=chunks, chars=chars,
                                            sidecar_files=sidecar),
                    model=a.model, model_bundled=bool(model_dir),
                    texts=texts, files={})
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
          f"{len(manifest['files'])} files")

if __name__ == "__main__":
    main(sys.argv[1:])

#!/usr/bin/env python3
"""Office documents (.rtf/.doc/.docx/.odt) -> Markdown, via LibreOffice headless.

LibreOffice exports to HTML (tables, headings and emphasis survive; a plain-text
export would flatten the amendment tables into unreadable runs), then `html_md`
turns that into the same Markdown flavour the PDF/OCR converters emit, so one
corpus reads consistently regardless of the source format.

Runs inside the `docconv` compose service — LibreOffice is not expected on the
host. From the research-stack directory:

    docker compose --profile tools run --rm docconv /corpus/<subdir> --dry-run
    docker compose --profile tools run --rm docconv /corpus/<subdir>

Output goes next to each source as `<name>.md` unless `--out` is given, and an
existing target is left alone unless `--force` is passed.

Usage:
    convert_office.py <file-or-dir> [...] [--out DIR] [--suffix .md]
                      [--force] [--dry-run] [--timeout SEC]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from html_md import html_to_md

EXTS = (".rtf", ".doc", ".docx", ".odt")
SOFFICE = os.environ.get("SOFFICE_BIN", "soffice")


def collect(paths, suffix, force):
    """Expand files/dirs into (source, target) pairs still needing conversion."""
    todo, skipped = [], []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            found = []
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    if f.lower().endswith(EXTS):
                        found.append(os.path.join(root, f))
            srcs = sorted(found)
        elif os.path.isfile(p) and p.lower().endswith(EXTS):
            srcs = [p]
        else:
            print(f"  skip (not an office file/dir): {p}", file=sys.stderr)
            continue
        for src in srcs:
            tgt = os.path.splitext(src)[0] + suffix
            if os.path.exists(tgt) and not force:
                skipped.append(src)
            else:
                todo.append((src, tgt))
    return todo, skipped


def to_html(src, workdir, timeout):
    """Convert one document to HTML in an isolated LibreOffice profile."""
    profile = os.path.join(workdir, "profile")
    outdir = os.path.join(workdir, "out")
    os.makedirs(outdir, exist_ok=True)
    cmd = [SOFFICE, f"-env:UserInstallation=file://{profile}", "--headless",
           "--norestore", "--convert-to", "html:HTML (StarWriter)",
           "--outdir", outdir, src]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    produced = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".html")
    if not os.path.isfile(produced):
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(err[-1] if err else "LibreOffice produced no HTML")
    with open(produced, "rb") as fh:
        raw = fh.read()
    # LibreOffice declares its charset in a meta tag; bs4 handles the decode.
    return raw.decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser(
        prog="convert_office.py",
        description="Convert .rtf/.doc/.docx/.odt to Markdown via LibreOffice headless.")
    ap.add_argument("paths", nargs="+", help="office files and/or directories")
    ap.add_argument("--out", default=None,
                    help="output dir (default: alongside each source document)")
    ap.add_argument("--suffix", default=".md", help="output suffix (default: .md)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing target (default: skip it)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be converted, convert nothing")
    ap.add_argument("--timeout", type=int, default=300,
                    help="per-document LibreOffice timeout in seconds")
    args = ap.parse_args()

    todo, skipped = collect(args.paths, args.suffix, args.force)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        todo = [(s, os.path.join(args.out, os.path.basename(t))) for s, t in todo]

    if skipped:
        print(f"{len(skipped)} already converted (pass --force to redo)")
    if not todo:
        print("nothing to convert")
        return 0

    if args.dry_run:
        for src, tgt in todo:
            print(f"  would write {tgt}  <-  {os.path.basename(src)}")
        print(f"{len(todo)} document(s) pending")
        return 0

    n_ok, failures = 0, []
    for i, (src, tgt) in enumerate(todo, 1):
        name = os.path.basename(src)
        workdir = tempfile.mkdtemp(prefix="docconv-")
        try:
            md = html_to_md(to_html(src, workdir, args.timeout))
            if not md.strip():
                raise RuntimeError("conversion produced empty Markdown")
            with open(tgt, "w") as fh:
                fh.write(md + "\n")
            n_ok += 1
            print(f"[{i}/{len(todo)}] {name} -> {os.path.basename(tgt)} ({len(md)} chars)")
        except Exception as exc:                       # noqa: BLE001 - report and continue
            failures.append((name, str(exc)))
            print(f"[{i}/{len(todo)}] {name}: FAILED - {exc}", file=sys.stderr)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nconverted {n_ok}/{len(todo)}")
    for name, err in failures:
        print(f"  FAILED {name}: {err}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Corpus-wide acquisition log: one record per source, merged across runs.

The corpus keeps a single `acquisition_log.json` at its root (see EXTRACT.md):
for every source, where it came from, when, by which route, and how its text
was extracted. Scripts that fetch or extract call `record(...)`; per-run
working files (acquire/retry logs, text manifests) stay in the scratch
directory and never enter the corpus.

    ACQUISITION_LOG=<corpus>/acquisition_log.json   # unset: record() is a no-op

A record is keyed by its Zotero item key when it has one, else by `path`
(relative to the corpus root). New scalar values overwrite old ones,
`attempts` are appended, and `retrieved` defaults to today. Absolute paths
are stored corpus-relative, Zotero-storage-relative or `~`-relative, never
with the host's home.
"""
import datetime, json, os, tempfile

LOG = os.environ.get("ACQUISITION_LOG")


def portable(p):
    """Corpus-relative, `storage/…` or `~/…` form of a path; never the home dir."""
    if not p:
        return p
    ap = os.path.abspath(os.path.expanduser(p))
    if LOG:
        root = os.path.dirname(os.path.abspath(LOG))
        if ap.startswith(root + os.sep):
            return os.path.relpath(ap, root)
    if "/Zotero/storage/" in ap:
        return "storage/" + ap.split("/Zotero/storage/", 1)[1]
    home = os.path.expanduser("~")
    return "~" + ap[len(home):] if ap.startswith(home + os.sep) else p


def record(**fields):
    """Merge one source's fields into the log. Returns silently when unset."""
    if not LOG:
        return
    fields = {k: v for k, v in fields.items() if v not in (None, "", [], {})}
    for k in ("path", "stored", "text"):
        if k in fields:
            fields[k] = portable(fields[k])
    ident = ("zotero", fields["zotero"]) if fields.get("zotero") else ("path", fields.get("path"))
    if not ident[1]:
        return
    data = json.load(open(LOG, encoding="utf-8")) if os.path.exists(LOG) else []
    rec = next((r for r in data if r.get(ident[0]) == ident[1]), None)
    if rec is None:
        rec = {}
        data.append(rec)
    for k, v in fields.items():
        if k == "attempts":
            rec.setdefault("attempts", []).extend(v)
        else:
            rec[k] = v
    rec.setdefault("retrieved", datetime.date.today().isoformat())
    data.sort(key=lambda r: (r.get("path") or "~", r.get("zotero", "")))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(LOG)), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, LOG)

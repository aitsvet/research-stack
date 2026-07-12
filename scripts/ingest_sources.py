#!/usr/bin/env python3
"""Ingest an explicit, mixed list of sources into a Zotero collection + emit a
paginated-markdown note per item. Domain-agnostic: ALL specifics (which sources,
filenames, item keys, collection) come from the manifest + CLI — nothing is
hardcoded here. This is the reusable form of per-project one-shot ingest scripts.

Use this when you already KNOW the exact sources (vs `discover.py`, which is for
OpenAlex/arXiv *search* discovery and DOI-bootstrap). Composes the rest of the
stack: arXiv Atom metadata, extract_texts.py (pdf->md), fetch_pdf.sh (web->pdf),
and zotero_mcp helpers (create/update/import/note/local-serve).

    source research-stack/.env
    ingest_sources.py manifest.json --collection KEY \
        --pdfdir DIR --notesdir DIR --state FILE [--serve-ip IP] [--only ID ...]

manifest.json: list of entries. Common fields:
  id        stable key for state/--only (required)
  kind      arxiv | pdfurl | web | localpdf | meta
  itemType  Zotero type (preprint/conferencePaper/journalArticle/...); default preprint
  slug      filename slug for the pdf/md (default = id)
  item_key  attach to this EXISTING item instead of creating one
  replace_notes  trash the item's current notes before adding the new one
  tags      extra tags (list)
Per kind:
  arxiv     arxiv: "2503.09089"           (title/creators/date auto from Atom API)
  pdfurl    pdf_url: public PDF URL        + title/creators/date/venue/doi/extra
  localpdf  pdf_path: /abs/local.pdf       + title/... (needs --serve-ip)
  web       url: page URL (rendered via fetch_pdf.sh; live page attached as snapshot)
  meta      (no full text; metadata-only item)        + title/creators/...
Metadata for non-arxiv kinds: title, date, creators [[last,first],...], venue,
doi, extra. A creators entry may instead be a full Zotero creator dict
({creatorType, lastName/firstName or name}) for editors/translators/corporate
names. Optional `fields` (raw Zotero field dict, e.g. publisher/place/ISBN for
books) is merged over the built-in ones. Resumable via --state (skips ids that
already have a key+note).
"""
import os, sys, json, argparse, subprocess, shutil, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET

ZS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ZS, "scripts"))
from zotero_mcp import MCP, add_note_file, import_file, import_local_files, item_key, result_text  # noqa

EXTRACT = os.path.join(ZS, "scripts", "extract_texts.py")
FETCHPDF = os.path.join(ZS, "scripts", "fetch_pdf.sh")
VENV_PY = os.path.join(ZS, ".venv", "bin", "python")
ATOM = "{http://www.w3.org/2005/Atom}"
UA = {"User-Agent": "Mozilla/5.0 (ingest_sources)"}


def http(url, timeout=60, binary=False):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read() if binary else r.read().decode("utf-8", "replace")


def arxiv_meta(aid):
    q = "https://export.arxiv.org/api/query?id_list=%s&max_results=1" % aid
    e = ET.fromstring(http(q, 30)).find(ATOM + "entry")
    if e is None or e.find(ATOM + "title") is None:
        return None
    title = " ".join(e.findtext(ATOM + "title").split())
    date = (e.findtext(ATOM + "published") or "")[:10]
    creators = []
    for a in e.findall(ATOM + "author"):
        nm = (a.findtext(ATOM + "name") or "").strip()
        if nm:
            p = nm.split(); creators.append((p[-1], " ".join(p[:-1])))
    return title, creators[:25], date


def pdf_to_md(pdf, out):
    subprocess.run([VENV_PY, EXTRACT, "md", pdf, "--out", out, "--suffix", ".md"],
                   capture_output=True, text=True, timeout=300)
    m = os.path.join(out, os.path.splitext(os.path.basename(pdf))[0] + ".md")
    return m if os.path.exists(m) and os.path.getsize(m) > 200 else None


def valid_pdf(p):
    return os.path.exists(p) and os.path.getsize(p) > 5000 and open(p, "rb").read(5) == b"%PDF-"


def build_fields(itype, title, date, url=None, doi=None, venue=None, extra=None):
    f = {"title": title, "date": date or ""}
    if url: f["url"] = url
    if doi: f["DOI"] = doi
    if venue:
        f[{"journalArticle": "publicationTitle", "conferencePaper": "proceedingsTitle",
           "bookSection": "bookTitle"}.get(itype, "websiteTitle")] = venue
    if extra: f["extra"] = extra
    return f


def note_keys(db, parent_key):
    import sqlite3
    c = sqlite3.connect("file:%s?mode=ro&immutable=1" % db, uri=True)
    pid = c.execute("SELECT itemID FROM items WHERE key=?", (parent_key,)).fetchone()
    if not pid:
        return []
    return [r[0] for r in c.execute(
        "SELECT i.key FROM itemNotes n JOIN items i ON n.itemID=i.itemID WHERE n.parentItemID=?",
        (pid[0],))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--pdfdir", required=True)
    ap.add_argument("--notesdir", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--serve-ip", default=None, help="public host IP for localpdf attach")
    ap.add_argument("--serve-port", type=int, default=28765)
    ap.add_argument("--db", default=os.path.join(ZS, "config", "Zotero", "zotero.sqlite"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()

    os.makedirs(a.pdfdir, exist_ok=True); os.makedirs(a.notesdir, exist_ok=True)
    entries = json.load(open(a.manifest))
    state = json.load(open(a.state)) if os.path.exists(a.state) else {}
    save = lambda: json.dump(state, open(a.state, "w"), indent=1, ensure_ascii=False)
    mcp = MCP()
    local_jobs = []  # (md_pdf_path, key, title) deferred to one serve session

    for e in entries:
        eid = e["id"]
        if a.only and eid not in a.only:
            continue
        st = state.get(eid, {})
        if st.get("key") and (st.get("md") or e.get("kind") == "meta"):
            print("skip(done):", eid); continue
        print("\n===", eid, "[", e.get("kind"), "]")
        rec = dict(st); rec["kind"] = e.get("kind")
        try:
            itype = e.get("itemType", "preprint")
            kind = e.get("kind", "meta")
            slug = e.get("slug", eid)
            title = e.get("title"); date = e.get("date"); venue = e.get("venue")
            creators = e.get("creators", []); doi = e.get("doi"); extra = e.get("extra")
            url = e.get("url"); pdf_url = e.get("pdf_url"); pdf_path = e.get("pdf_path")

            if kind == "arxiv":
                m = arxiv_meta(e["arxiv"])
                if not m:
                    rec["error"] = "arxiv meta not found"; state[eid] = rec; save(); continue
                title, creators, date = m
                url = "https://arxiv.org/abs/%s" % e["arxiv"]
                pdf_url = "https://arxiv.org/pdf/%s" % e["arxiv"]
                extra = ("arXiv:%s" % e["arxiv"]) + (("; " + venue) if venue else "")

            # ---- ensure item ----
            key = e.get("item_key") or st.get("key")
            if not key:
                fields = build_fields(itype, title, date, url=url, doi=doi, venue=venue, extra=extra)
                fields.update(e.get("fields", {}))
                cr = [c if isinstance(c, dict) else
                      {"creatorType": "author", "lastName": c[0], "firstName": c[1]} for c in creators]
                resp = mcp.call("create_item", {"itemType": itype, "fields": fields, "creators": cr,
                                                "tags": e.get("tags", []), "collections": [a.collection]})
                key = item_key(resp)
                if not key:
                    rec["error"] = "create failed: " + result_text(resp)[:120]
                    print(" ", rec["error"]); state[eid] = rec; save(); continue
            rec["key"] = key; print("  item", key)
            base = "%s_%s" % (key, slug); md = None

            # ---- acquire full text + attach PDF ----
            if kind in ("arxiv", "pdfurl"):
                pdf = os.path.join(a.pdfdir, base + ".pdf")
                try:
                    open(pdf, "wb").write(http(pdf_url, 90, binary=True))
                except Exception as ex:
                    print("  dl fail", ex)
                if valid_pdf(pdf):
                    md = pdf_to_md(pdf, a.pdfdir)
                    import_file(mcp, key, pdf_url, title[:120], "application/pdf", if_exists="skip")
                    rec["pdf"] = "attached"
                else:
                    rec["pdf"] = "no-pdf"
            elif kind == "localpdf":
                if not valid_pdf(pdf_path or ""):
                    rec["pdf"] = "bad-localpdf"
                else:
                    pdf = os.path.join(a.pdfdir, base + ".pdf")
                    if os.path.abspath(pdf_path) != os.path.abspath(pdf):
                        shutil.copyfile(pdf_path, pdf)
                    md = pdf_to_md(pdf, a.pdfdir)
                    if a.serve_ip:
                        local_jobs.append((pdf, key, title[:120])); rec["pdf"] = "queued"
                    else:
                        rec["pdf"] = "no-serve-ip"
            elif kind == "web":
                subprocess.run(["bash", FETCHPDF, url, a.pdfdir, base], capture_output=True, timeout=160)
                cand = os.path.join(a.pdfdir, base + ".md")
                md = cand if os.path.exists(cand) and os.path.getsize(cand) > 200 else None
                try:
                    import_file(mcp, key, url, title[:120]); rec["pdf"] = "snapshot"
                except Exception:
                    rec["pdf"] = "snapshot?"
            # kind == meta: nothing

            # ---- replace old notes ----
            if e.get("replace_notes"):
                olds = note_keys(a.db, key)
                if olds:
                    mcp.call("batch_trash", {"itemKeys": olds}); print("  trashed", olds)

            # ---- note + ingest copy ----
            if md and os.path.exists(md):
                dest = os.path.join(a.notesdir, key + ".md")
                shutil.copyfile(md, dest); rec["md"] = dest; rec["md_bytes"] = os.path.getsize(md)
                try:
                    add_note_file(mcp, key, md, title[:140], tags=["markdown-extract"])
                    rec["note"] = "ok"
                except urllib.error.HTTPError as ex:
                    rec["note"] = "HTTP %s (md in notesdir only)" % ex.code
                print("  md", rec.get("md_bytes"), "note", rec.get("note"))
            else:
                rec["md"] = None
            rec.pop("error", None); state[eid] = rec; save()
        except Exception as ex:
            rec["error"] = repr(ex)[:200]; state[eid] = rec; save(); print("  EXC", rec["error"])

    # ---- one serve session for all queued local PDFs ----
    if local_jobs:
        print("\nserving %d local PDFs from %s" % (len(local_jobs), a.serve_ip))
        for key, ok, t in import_local_files(mcp, local_jobs, a.serve_ip, a.serve_port):
            print("  localpdf", key, "OK" if ok else "FAIL", t[:60])
            for eid, r in state.items():
                if r.get("key") == key:
                    r["pdf"] = "attached" if ok else "attach-fail"
        save()

    print("\n==== SUMMARY ====")
    for e in entries:
        r = state.get(e["id"], {})
        print("%-16s key=%-9s pdf=%-11s note=%-6s md=%s%s" % (
            e["id"], r.get("key", "-"), str(r.get("pdf", "-")), str(r.get("note", "-")),
            "yes" if r.get("md") else "NO", "  ERR:" + r["error"] if r.get("error") else ""))


if __name__ == "__main__":
    main()

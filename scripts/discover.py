#!/usr/bin/env python3
"""Worldwide-discovery pass — two modes:

  (A) Search mode  — query OpenAlex (and optionally arXiv) per topic, dedupe by DOI,
      create lightweight Zotero items via MCP HTTP, and write a markdown candidate
      table per topic. Use when you don't yet know which papers exist.

      discover.py <topics.json>

      topics.json is a JSON array. Each element:
        {
          "id":        "A",                              # short tag; used in filenames + tags
          "key":       "<ZOTERO_COLLECTION_KEY>",        # target collection for created items
          "title":     "Human-readable topic title",
          "openalex":  "search query string",            # passed to OpenAlex `search=`
          "year_from": 2010,                             # publication-date lower bound
          "arxiv":     "abs:(...)"                       # optional; null/omit to skip arXiv
        }

  (B) DOI-list mode — given a JSON list of DOIs (often pulled from a draft paper's
      bibliography), look each one up in Crossref+OpenAlex, create the Zotero item
      with full metadata, add it to the target collection, and (best-effort) attach
      the OA PDF if OpenAlex's open_access.oa_url returns one.

      discover.py --dois <dois.json> <selected_collection_key> [<topic_id>]

      dois.json is a JSON array of either bare DOI strings or objects:
        ["10.3389/fpubh.2020.00457",
         {"doi": "10.1108/IMEFM-02-2025-0088", "tag": "survey-ref-18"}]

Env:
  ZOTERO_MCP_TOKEN  required, bearer token for the Zotero MCP plugin.
  OPENALEX_EMAIL    optional, polite-pool identifier (default: $ZOTERO_USER_EMAIL or anonymous).
  DISCOVERY_OUT     optional, output dir (default: ~/zotero-setup/.discovery).
"""
import json, urllib.request, urllib.parse, sys, time, os, re
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from zotero_mcp import MCP

EMAIL  = os.environ.get("OPENALEX_EMAIL") or os.environ.get("ZOTERO_USER_EMAIL", "")
UA     = f"zotero-setup/1.0 (mailto:{EMAIL})" if EMAIL else "zotero-setup/1.0"
OUTDIR = os.environ.get("DISCOVERY_OUT", os.path.expanduser("~/zotero-setup/.discovery"))
os.makedirs(OUTDIR, exist_ok=True)

# ----------------------------- HTTP helpers --------------------------------

def http_json(req, retries=3, delay=2.0):
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(delay)
    raise last

def http_text(url, retries=3, delay=2.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            last = e
            time.sleep(delay)
    raise last

# ----------------------------- MCP -----------------------------------------

def cap_abstract(args, limit=3500):
    """The MCP plugin rejects request bodies over ~3700 bytes with a misleading
    -32700 Parse error. Shrink abstractNote until the create_item payload fits.
    ensure_ascii=False keeps UTF-8 bytes compact (no \\uXXXX bloat for
    non-Latin abstracts)."""
    def size():
        payload = {"jsonrpc":"2.0","id":10**8,"method":"tools/call",
                   "params":{"name":"create_item","arguments":args}}
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    fields = args.get("fields", {})
    if "abstractNote" in fields:
        while size() > limit and len(fields["abstractNote"]) > 50:
            fields["abstractNote"] = fields["abstractNote"][:max(50, len(fields["abstractNote"]) - 200)]
        if size() > limit:
            fields["abstractNote"] = "[abstract omitted — exceeded MCP body limit]"
    return args

# ----------------------------- OpenAlex ------------------------------------

OA_SELECT = ",".join(["id","doi","title","publication_year","publication_date",
                      "authorships","primary_location","abstract_inverted_index",
                      "open_access","type","cited_by_count","relevance_score"])

def openalex_search(query, year_from, per_page=30):
    params = {
        "search": query,
        "per-page": per_page,
        "filter": f"from_publication_date:{year_from}-01-01,type:article|book-chapter|review",
        "select": OA_SELECT,
    }
    if EMAIL: params["mailto"] = EMAIL
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return http_json(req)["results"]

def reconstruct_abstract(inv):
    if not inv: return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))

def oa_to_candidate(w):
    auth = [a.get("author", {}).get("display_name","") for a in (w.get("authorships") or [])[:6]]
    venue = (((w.get("primary_location") or {}).get("source") or {}).get("display_name")) or ""
    abstract = reconstruct_abstract(w.get("abstract_inverted_index") or {})
    doi = (w.get("doi") or "").replace("https://doi.org/","")
    oa = w.get("open_access") or {}
    return {
        "source": "openalex",
        "openalex_id": w.get("id",""),
        "doi": doi,
        "title": w.get("title") or "",
        "abstract": abstract,
        "year": w.get("publication_year"),
        "date": w.get("publication_date") or "",
        "venue": venue,
        "authors": auth,
        "is_oa": bool(oa.get("is_oa")),
        "oa_status": oa.get("oa_status") or "closed",
        "oa_url": oa.get("oa_url") or "",
        "type": w.get("type") or "article",
        "cited_by": w.get("cited_by_count") or 0,
        "relevance": w.get("relevance_score") or 0.0,
        "arxiv_id": "",
    }

# ----------------------------- arXiv ---------------------------------------

ATOM = "{http://www.w3.org/2005/Atom}"

def arxiv_search(query, max_results=10):
    url = ("http://export.arxiv.org/api/query?"
           + urllib.parse.urlencode({"search_query": query,
                                      "max_results": max_results,
                                      "sortBy":"relevance","sortOrder":"descending"}))
    xml = http_text(url)
    root = ET.fromstring(xml)
    out = []
    for entry in root.findall(ATOM+"entry"):
        title = (entry.findtext(ATOM+"title") or "").strip().replace("\n"," ")
        summary = (entry.findtext(ATOM+"summary") or "").strip().replace("\n"," ")
        eid = entry.findtext(ATOM+"id") or ""
        # canonical arxiv id like 2401.01234
        m = re.search(r"abs/([\w./-]+?)(v\d+)?$", eid)
        arxiv_id = m.group(1) if m else eid
        # try to find a DOI link
        doi = ""
        for ln in entry.findall(ATOM+"link"):
            if ln.get("title") == "doi":
                doi = ln.get("href","").replace("http://dx.doi.org/","").replace("https://doi.org/","")
        published = entry.findtext(ATOM+"published") or ""
        authors = [a.findtext(ATOM+"name") for a in entry.findall(ATOM+"author")][:6]
        out.append({
            "source":"arxiv",
            "openalex_id":"",
            "doi": doi,
            "title": title,
            "abstract": summary,
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "date": published[:10],
            "venue":"arXiv",
            "authors": authors,
            "is_oa": True,
            "oa_status":"green",
            "oa_url": eid.replace("/abs/","/pdf/") + ".pdf" if "abs" in eid else eid,
            "type":"preprint",
            "cited_by": 0,
            "relevance": 0.0,
            "arxiv_id": arxiv_id,
        })
    return out

# ----------------------------- Zotero create -------------------------------

def zotero_item_args(c, collection_key, topic_id):
    creators = []
    for a in c["authors"]:
        if not a: continue
        parts = a.rsplit(" ",1)
        if len(parts) == 2:
            creators.append({"creatorType":"author","firstName":parts[0],"lastName":parts[1]})
        else:
            creators.append({"creatorType":"author","lastName":a})
    item_type = "preprint" if c["source"]=="arxiv" else "journalArticle"
    fields = {
        "title": c["title"][:400],
        "date": c["date"] or (str(c["year"]) if c["year"] else ""),
        "language": "en",
        # Zotero MCP plugin has ~3700-byte body limit; cap abstract aggressively.
        "abstractNote": c["abstract"][:2000],
    }
    if c["doi"]: fields["DOI"] = c["doi"]
    if c["venue"]: fields["publicationTitle"] = c["venue"]
    if c["oa_url"]: fields["url"] = c["oa_url"]
    extra_lines = []
    if c["openalex_id"]: extra_lines.append(f"OpenAlex: {c['openalex_id']}")
    if c["arxiv_id"]: extra_lines.append(f"arXiv: {c['arxiv_id']}")
    extra_lines.append(f"OA status: {c['oa_status']}")
    if c["oa_url"]: extra_lines.append(f"OA URL: {c['oa_url']}")
    extra_lines.append(f"Source: {c['source']}")
    extra_lines.append(f"Cited by: {c['cited_by']}")
    extra_lines.append(f"Discovered: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    fields["extra"] = "\n".join(extra_lines)
    tags = [f"frontier:{topic_id}", f"oa:{c['oa_status']}", "candidate"]
    if c["source"]=="arxiv": tags.append("preprint")
    return {
        "itemType": item_type,
        "fields": fields,
        "creators": creators,
        "tags": tags,
        "collections": [collection_key],
    }

def extract_item_key(resp):
    try:
        text = resp["result"]["content"][0]["text"]
        return json.loads(text).get("itemKey","")
    except Exception:
        return ""

# ----------------------------- main ---------------------------------------

def rescale_relevance(cands):
    rels = [c["relevance"] for c in cands if c["source"]=="openalex"]
    if not rels: return
    lo, hi = min(rels), max(rels)
    for c in cands:
        if c["source"]=="openalex":
            c["score_hint"] = 1 + 4*((c["relevance"]-lo)/(hi-lo)) if hi>lo else 3
        else:
            c["score_hint"] = 3  # arxiv: neutral hint
    # round
    for c in cands:
        c["score_hint"] = round(c.get("score_hint",3), 1)

def md_table(cands, topic):
    rows = []
    rows.append(f"# Topic {topic['id']} — {topic['title']}\n")
    rows.append(f"OpenAlex query: `{topic['openalex']}`  ")
    rows.append(f"Year window: {topic['year_from']}→present  ")
    if topic.get("arxiv"): rows.append(f"arXiv query: `{topic['arxiv']}`")
    rows.append("")
    rows.append("| # | Score | OA | Year | Venue | Title | DOI / arXiv | Cited |")
    rows.append("|---|-------|----|------|-------|-------|-------------|-------|")
    for i,c in enumerate(cands, 1):
        ident = c["doi"] or c["arxiv_id"] or "—"
        ven = (c["venue"] or "")[:40]
        ttl = (c["title"] or "")[:90].replace("|","\\|")
        rows.append(f"| {i} | {c['score_hint']} | {c['oa_status']} | {c['year'] or '?'} | {ven} | {ttl} | {ident} | {c['cited_by']} |")
    return "\n".join(rows) + "\n"

# ----------------------------- DOI-list mode -------------------------------

def crossref_lookup(doi):
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/.()')}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        return http_json(req)["message"]
    except Exception:
        return None

def openalex_by_doi(doi):
    params = {"select": OA_SELECT}
    if EMAIL: params["mailto"] = EMAIL
    url = f"https://api.openalex.org/works/doi:{doi}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        return http_json(req)
    except Exception:
        return None

def merge_doi_metadata(doi, cr, oa):
    """Build a candidate dict from Crossref (authoritative authors / venue) plus
    OpenAlex (abstract, oa_url). Either source may be None."""
    title = ""
    authors = []
    venue = ""
    date = ""
    year = None
    if cr:
        title = (cr.get("title") or [""])[0]
        title = re.sub(r"<[^>]+>", "", title)
        venue = (cr.get("container-title") or [""])[0]
        for a in (cr.get("author") or []):
            fn = (a.get("given") or "").strip()
            ln = (a.get("family") or "").strip()
            if fn or ln:
                authors.append((fn + " " + ln).strip())
        dp = (cr.get("published-online") or cr.get("published-print") or {}).get("date-parts") or [[None]]
        if dp and dp[0]:
            parts = [str(p) for p in dp[0] if p is not None]
            if parts:
                date = "-".join(parts[:3])
                try: year = int(parts[0])
                except ValueError: year = None
    abstract = ""
    oa_url = ""
    oa_status = "closed"
    openalex_id = ""
    cited_by = 0
    if oa:
        abstract = reconstruct_abstract(oa.get("abstract_inverted_index") or {})
        oa_obj = oa.get("open_access") or {}
        oa_url = oa_obj.get("oa_url") or ""
        oa_status = oa_obj.get("oa_status") or "closed"
        openalex_id = oa.get("id") or ""
        cited_by = oa.get("cited_by_count") or 0
        if not year:
            year = oa.get("publication_year")
        if not date:
            date = oa.get("publication_date") or (str(year) if year else "")
        if not title:
            title = oa.get("title") or ""
        if not venue:
            venue = (((oa.get("primary_location") or {}).get("source") or {}).get("display_name")) or ""
    return {
        "source": "doi-list",
        "openalex_id": openalex_id,
        "doi": doi.lower(),
        "title": title,
        "abstract": abstract,
        "year": year,
        "date": date,
        "venue": venue,
        "authors": authors,
        "is_oa": bool(oa_url),
        "oa_status": oa_status,
        "oa_url": oa_url,
        "type": "journalArticle",
        "cited_by": cited_by,
        "relevance": 0.0,
        "arxiv_id": "",
    }

def doi_list_mode(dois_path, collection_key, topic_id):
    with open(dois_path) as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        sys.exit("dois.json must be a JSON array")
    mcp = MCP("discover", throttle=0.15)
    results = []
    for ent in entries:
        if isinstance(ent, str):
            doi, extra_tag = ent, None
        elif isinstance(ent, dict):
            doi = ent.get("doi") or ""
            extra_tag = ent.get("tag")
        else:
            continue
        doi = doi.strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")
        if not doi:
            continue
        cr = crossref_lookup(doi)
        oa = openalex_by_doi(doi)
        if not cr and not oa:
            print(f"  [{doi}] not found in Crossref or OpenAlex", file=sys.stderr)
            results.append({"doi": doi, "itemKey": "", "error": "not-found"})
            continue
        c = merge_doi_metadata(doi, cr, oa)
        args = zotero_item_args(c, collection_key, topic_id)
        if extra_tag:
            args["tags"].append(extra_tag)
        resp = mcp.call("create_item", cap_abstract(args))
        item_key = extract_item_key(resp)
        att_key = ""
        if item_key and c["oa_url"]:
            att_resp = mcp.call("import_attachment_url", {
                "url": c["oa_url"],
                "parentItemKey": item_key,
                "contentType": "application/pdf",
            })
            try:
                att_payload = json.loads(att_resp["result"]["content"][0]["text"])
                att_key = att_payload.get("itemKey") or att_payload.get("details", {}).get("attachmentKey", "")
            except Exception:
                att_key = ""
        results.append({"doi": doi, "itemKey": item_key, "attachmentKey": att_key,
                         "title": c["title"][:80], "oa_url": c["oa_url"]})
        print(f"  [{doi}] item={item_key or '!FAIL'} att={att_key or '-'} | {c['title'][:60]}", file=sys.stderr)
    out_path = os.path.join(OUTDIR, f"doi_ingest_{collection_key}.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {out_path}  ({sum(1 for r in results if r.get('itemKey'))}/{len(results)} created)", file=sys.stderr)

def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "--dois":
        # discover.py --dois <dois.json> <collection_key> [<topic_id>]
        topic_id = sys.argv[4] if len(sys.argv) > 4 else "ingest"
        doi_list_mode(sys.argv[2], sys.argv[3], topic_id)
        return
    if len(sys.argv) < 2:
        sys.exit("usage: discover.py <topics.json>\n       discover.py --dois <dois.json> <collection_key> [<topic_id>]")
    with open(sys.argv[1]) as f:
        topics = json.load(f)
    if not isinstance(topics, list):
        sys.exit("topics.json must be a JSON array of topic objects")
    mcp = MCP("discover", throttle=0.15)
    summary = []
    for t in topics:
        print(f"[{t['id']}] OpenAlex …", file=sys.stderr)
        works = openalex_search(t["openalex"], t["year_from"], per_page=30)
        cands = [oa_to_candidate(w) for w in works]
        if t.get("arxiv"):
            print(f"[{t['id']}] arXiv …", file=sys.stderr)
            try:
                ax = arxiv_search(t["arxiv"], max_results=10)
                # dedupe by DOI/arxiv-id against OpenAlex set
                seen_dois = {c["doi"] for c in cands if c["doi"]}
                for c in ax:
                    if c["doi"] and c["doi"] in seen_dois: continue
                    cands.append(c)
            except Exception as e:
                print(f"  arXiv failed: {e}", file=sys.stderr)
        rescale_relevance(cands)
        # sort by score_hint desc
        cands.sort(key=lambda c: -c["score_hint"])
        # create Zotero items
        for c in cands:
            args = zotero_item_args(c, t["key"], t["id"])
            resp = mcp.call("create_item", cap_abstract(args))
            c["zotero_key"] = extract_item_key(resp)
            if not c["zotero_key"]:
                print(f"  create_item failed: {resp!r}"[:200], file=sys.stderr)
        # write per-topic table
        out_path = os.path.join(OUTDIR, f"topic_{t['id']}.md")
        with open(out_path, "w") as f:
            f.write(md_table(cands, t))
        print(f"[{t['id']}] wrote {out_path}  ({len(cands)} candidates)", file=sys.stderr)
        summary.append((t['id'], t['title'], len(cands), out_path))
    # global summary
    with open(os.path.join(OUTDIR, "summary.md"), "w") as f:
        f.write("# Discovery pass summary\n\n")
        for tid, ttl, n, p in summary:
            f.write(f"- **{tid}** {ttl}: {n} candidates → `{p}`\n")
    print("done", file=sys.stderr)

if __name__ == "__main__":
    main()

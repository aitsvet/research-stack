#!/usr/bin/env python3
"""Acquire PDFs for every item in a Zotero collection that has an OA URL
recorded in its 'extra' field (set by discover.py). Calls Zotero MCP's
import_attachment_url so the PDF lands as a child attachment.

Skips items with no OA URL (already `OA status: closed`). Writes an
acquire log as JSON for retry_unpaywall.py to pick up.

Usage:
  acquire.py <collectionKey> [<acquire_log.json>]

Defaults log to <DISCOVERY_OUT>/acquire_<collectionKey>.json.

Env:
  ZOTERO_MCP_TOKEN required.
  ZOTERO_MCP_URL   optional MCP URL override.
  DISCOVERY_OUT    optional output dir (default ~/zotero-setup/.discovery).
"""
import json, os, sys, re

from zotero_mcp import MCP, result_json

DISC = os.environ.get("DISCOVERY_OUT", os.path.expanduser("~/zotero-setup/.discovery"))
os.makedirs(DISC, exist_ok=True)

def extract_oa(extra):
    m = re.search(r"OA URL:\s*(\S+)", extra or "")
    return m.group(1).strip() if m else ""

def extract_status(extra):
    m = re.search(r"OA status:\s*(\S+)", extra or "")
    return m.group(1).strip() if m else ""

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: acquire.py <collectionKey> [<acquire_log.json>]")
    ck = sys.argv[1]
    log_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DISC, f"acquire_{ck}.json")
    mcp = MCP("acquire", throttle=0.4)
    coll = result_json(mcp.call("get_collection_items", {"collectionKey": ck, "limit": 200}))
    if coll is None:
        sys.exit("failed to read collection")
    items = coll.get("data") if isinstance(coll, dict) else None
    if items is None and isinstance(coll, dict): items = coll.get("items", [])
    if items is None: items = coll
    log = []
    for it in items:
        d = it.get("data", it)
        key = d.get("key") or it.get("key")
        title = (d.get("title") or "")[:70]
        extra = d.get("extra") or ""
        oa_url = extract_oa(extra)
        oa_status = extract_status(extra)
        if not oa_url:
            log.append({"key": key, "title": title, "result": "skip:no_oa_url", "oa_status": oa_status})
            print(f"  - {key}  SKIP (no OA URL)  {title}")
            continue
        # Choose contentType for PDFs vs arXiv etc.
        ct = "application/pdf"
        # arXiv abs URLs need rewriting to pdf
        if "arxiv.org/abs/" in oa_url:
            oa_url = oa_url.replace("/abs/", "/pdf/")
        if oa_url.endswith(".pdf") or "arxiv.org/pdf/" in oa_url:
            ct = "application/pdf"
        elif oa_url.endswith(".html") or "doi.org" in oa_url:
            ct = None  # let Zotero pick the right path
        args = {"url": oa_url, "parentItemKey": key, "ifExists": "skip"}
        if ct: args["contentType"] = ct
        try:
            r = mcp.call("import_attachment_url", args, timeout=300)
        except Exception as e:
            log.append({"key": key, "title": title, "result": "FAIL:exception", "error": str(e)[:200], "url": oa_url})
            print(f"  + {key}  FAIL exception  {title}  ({type(e).__name__})")
            # Save log after each item so we don't lose progress
            with open(log_path,"w") as f:
                json.dump(log, f, ensure_ascii=False, indent=2)
            continue
        if r and r.get("error"):
            log.append({"key": key, "title": title, "result": "FAIL:timeout", "error": r["error"].get("message","")[:200], "url": oa_url})
            print(f"  + {key}  FAIL timeout    {title}")
        else:
            result = result_json(r)
            success = bool(result and result.get("success"))
            decision = (result or {}).get("details", {}).get("decision", "?")
            attach = (result or {}).get("itemKey", "")
            log.append({"key": key, "title": title, "result": f"{'OK' if success else 'FAIL'}:{decision}", "attach": attach, "url": oa_url})
            print(f"  + {key}  {('OK' if success else 'FAIL'):4} {decision:10}  {title}")
        with open(log_path,"w") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    # Write log
    with open(log_path,"w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    n_ok = sum(1 for r in log if r["result"].startswith("OK"))
    n_skip = sum(1 for r in log if r["result"].startswith("skip"))
    print(f"\n{n_ok} acquired, {n_skip} skipped, {len(log)-n_ok-n_skip} failed of {len(log)} items")

if __name__=="__main__":
    main()

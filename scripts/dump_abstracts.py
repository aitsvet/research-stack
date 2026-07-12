#!/usr/bin/env python3
"""Fetch all items in one or more Zotero collections and dump itemKey + title +
OA status + abstract to per-collection markdown files. Lets a reader triage many
abstracts in one place without re-querying MCP.

Usage:
  dump_abstracts.py <collectionKey> [<collectionKey> ...]

Writes <DISCOVERY_OUT>/abstracts_<collectionKey>.md per arg.

Env:
  ZOTERO_MCP_TOKEN required.
  ZOTERO_MCP_URL   optional override (default http://127.0.0.1:23120/mcp).
  DISCOVERY_OUT    optional output dir (default ~/research-stack/.discovery).
"""
import os, sys

from zotero_mcp import MCP, result_json

DISC = os.environ.get("DISCOVERY_OUT", os.path.expanduser("~/research-stack/.discovery"))
os.makedirs(DISC, exist_ok=True)

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: dump_abstracts.py <collectionKey> [<collectionKey> ...]")
    mcp = MCP("dump", throttle=0.2)
    for ck in sys.argv[1:]:
        # Try to look up the collection's display name; fall back to the key.
        det = result_json(mcp.call("get_collection_details", {"collectionKey": ck}))
        name = (det or {}).get("name") or (det or {}).get("data",{}).get("name") or ck
        resp = mcp.call("get_collection_items", {"collectionKey": ck, "limit": 500, "mode":"standard"})
        data = result_json(resp) or {}
        items = data.get("data") if isinstance(data, dict) else None
        if items is None:
            items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = items.get("items", [])
        out_path = f"{DISC}/abstracts_{ck}.md"
        with open(out_path, "w") as f:
            f.write(f"# {name}  ({ck})\n\nTotal candidates: {len(items)}\n\n")
            for i, it in enumerate(items, 1):
                d = it.get("data", it)
                key = d.get("key") or it.get("key", "?")
                title = d.get("title", "")
                year  = (d.get("date") or "")[:4]
                venue = d.get("publicationTitle", "")
                doi   = d.get("DOI", "")
                abst  = d.get("abstractNote", "")
                extra = d.get("extra", "")
                # OA from extra
                oa = ""
                for line in extra.splitlines():
                    if line.startswith("OA status:"):
                        oa = line.split(":",1)[1].strip(); break
                f.write(f"## [{i}] {key}  ({oa}, {year})\n")
                f.write(f"**{title}**  \n")
                if venue: f.write(f"*{venue}*  \n")
                if doi: f.write(f"DOI: {doi}  \n")
                f.write(f"\n{abst}\n\n---\n\n")
        print(f"{ck} ({name}): {len(items)} items → {out_path}")

if __name__=="__main__":
    main()

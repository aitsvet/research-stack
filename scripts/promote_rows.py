#!/usr/bin/env python3
"""Promote selected rows from a discovery candidate table (as written by
discover.py — `# | Score | OA | Year | Venue | Title | DOI/arXiv | Cited`) into
a target Zotero collection. Resolves each row's DOI / arXiv identifier against
items in the source collection to find the Zotero itemKey, then adds those
itemKeys to the target.

Usage:
  promote_rows.py <targetCollection> <sourceCollection> <table.md> <row1>[,row2,...]

Example:
  promote_rows.py TECH123 SRC456 .discovery/topic_A.md 1,3,5,7
  promote_rows.py TECH123 SRC456 .discovery/topic_A.md --rows-from rows.txt

Env:
  ZOTERO_MCP_TOKEN  required.
  ZOTERO_MCP_URL    optional MCP URL override.
"""
import re, sys

from zotero_mcp import MCP, result_json

def parse_table(path):
    """Parse a discovery candidate table. Splits on '|' rather than regex —
    DOIs can contain parens (e.g. 10.46298/lmcs-18(4:12)2022)."""
    rows = []
    with open(path) as f:
        for line in f:
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 8 or not cells[0].isdigit():
                continue
            no, _score, oa, _year, _venue, title, ident, _cited = cells[:8]
            rows.append({"row": int(no), "oa": oa, "title": title, "ident": ident})
    return rows

def parse_rows_arg(arg):
    """Accept '1,3,5' or '1-5,8' or '@file' (one row per line)."""
    if arg.startswith("@"):
        with open(arg[1:]) as f:
            return sorted({int(x) for x in f.read().split() if x.isdigit()})
    out = set()
    for piece in arg.split(","):
        piece = piece.strip()
        if "-" in piece:
            a, b = piece.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif piece.isdigit():
            out.add(int(piece))
    return sorted(out)

def main():
    if len(sys.argv) < 5:
        sys.exit("usage: promote_rows.py <targetCollection> <sourceCollection> <table.md> <rows>")
    target, source, table_path, rows_arg = sys.argv[1:5]
    target_rows = set(parse_rows_arg(rows_arg))
    tbl = parse_table(table_path)
    picked = [r for r in tbl if r["row"] in target_rows]
    print(f"selected {len(picked)} of {len(target_rows)} target rows from {len(tbl)} table rows", file=sys.stderr)

    mcp = MCP("promote", throttle=0.25)
    coll = result_json(mcp.call("get_collection_items", {"collectionKey": source, "limit": 500}))
    items = coll.get("data") if isinstance(coll, dict) else None
    if items is None and isinstance(coll, dict): items = coll.get("items", [])
    if items is None: items = coll

    # Build ident → itemKey map (DOI lowercased, arXiv ID without version)
    idmap = {}
    for it in items:
        d = it.get("data", it)
        key = d.get("key") or it.get("key")
        doi = (d.get("DOI") or "").strip().lower()
        extra = d.get("extra") or ""
        m = re.search(r"arXiv:\s*([\w./-]+)", extra)
        arx = (m.group(1) if m else "").lower()
        if doi: idmap[doi] = key
        if arx:
            idmap[arx] = key
            idmap[arx.split("v")[0]] = key

    resolved, missing = [], []
    for p in picked:
        ident = p["ident"].lower()
        k = idmap.get(ident) or idmap.get(ident.split("v")[0])
        if k:
            resolved.append((p, k))
        else:
            missing.append(p)

    for p in missing:
        print(f"  ?  row {p['row']:>3}  no Zotero item matches ident={p['ident']!r}  title={p['title'][:50]!r}", file=sys.stderr)

    for p, k in resolved:
        r = mcp.call("add_to_collection", {"itemKey": k, "collectionKey": target})
        ok = bool(r and r.get("result") and not r["result"].get("isError"))
        print(f"  +  row {p['row']:>3}  {k}  {p['title'][:60]:<60} {'OK' if ok else 'FAIL'}", file=sys.stderr)

    print(f"{len(resolved)} added, {len(missing)} unresolved", file=sys.stderr)

if __name__ == "__main__":
    main()

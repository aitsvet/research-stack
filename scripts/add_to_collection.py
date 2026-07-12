#!/usr/bin/env python3
"""Add an explicit list of Zotero itemKeys to a target collection.

Replaces the earlier row-number-table parser approach — directly take itemKeys,
which is what you actually decide on after reading abstracts.

Usage:
  python3 add_to_collection.py <targetCollectionKey> <itemKey1> [itemKey2 ...]
  python3 add_to_collection.py <targetCollectionKey> --stdin   # one key per line

Idempotent: re-adding an item to a collection is a no-op.
"""
import sys

from zotero_mcp import MCP

def main():
    if len(sys.argv) < 3:
        sys.exit("usage: add_to_collection.py <collectionKey> <itemKey...> | --stdin")
    target = sys.argv[1]
    if sys.argv[2] == "--stdin":
        keys = [ln.strip() for ln in sys.stdin if ln.strip()]
    else:
        keys = sys.argv[2:]
    mcp = MCP("add", throttle=0.25)
    ok = 0
    for k in keys:
        r = mcp.call("add_to_collection", {"itemKey": k, "collectionKey": target})
        success = r and r.get("result") and not r["result"].get("isError")
        print(f"  {k} -> {'OK' if success else 'FAIL'}")
        if success: ok += 1
    print(f"{ok}/{len(keys)} added to {target}")

if __name__=="__main__":
    main()

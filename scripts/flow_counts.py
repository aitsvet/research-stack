#!/usr/bin/env python3
"""Print PRISMA-style flow numbers for a paper's discovery folder.

Reads four sources of truth:

  1. Top collection size  (the paper's parent collection)
     → "candidates" after the initial OpenAlex/arXiv discovery pass.
  2. `selected` subcollection size
     → "unique items included after triage".
  3. `needs-manual-access` subcollection size
     → "items still pending acquisition (paywall / Cloudflare)".
  4. `text_manifest_<selected_key>.json`
     → "items with extracted full text" (= entries where `size > 0`).
  5. (Optional) `triage.json`
     → "gross slice-level picks" (sum across all slice groups, before dedup).
     Use this to identify the gap between slice-pick count and unique-included
     count: those numbers WILL differ when papers appear in more than one slice.

Use this any time a draft paper claims a PRISMA-style count ("N candidates →
M included → K read in depth") to confirm the numbers match the artefacts
before submission. A common drift: triage records gross slice picks while the
paper's text quotes the same number as if it were the unique-item count, and
the "deep-read" count is missing from any artefact entirely.

Usage:
  flow_counts.py <top_collection_key> [<selected_key> [<needs_key>]]
  flow_counts.py --discovery <discovery_dir> [<top_collection_key>]

The `--discovery` form reads triage.json and the text manifest from the local
discovery folder; no MCP calls. Use it when offline or when the Zotero stack
isn't running.

Outputs a plain-text table to stdout suitable for pasting into a paper's
PRISMA-flow figure or a `coverage.md` note.

Env:
  ZOTERO_MCP_TOKEN  required for collection-size lookups.
  ZOTERO_MCP_URL    optional MCP URL override.
  DISCOVERY_OUT     optional; only used as a fallback if no path passed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from zotero_mcp import MCP, result_json


def collection_size(mcp: MCP, key: str) -> int:
    """Ask the MCP for a `get_collection_items` count."""
    resp = mcp.call("get_collection_items",
                    {"collectionKey": key, "mode": "minimal", "limit": 1000})
    try:
        payload = result_json(resp)
        meta = payload.get("metadata") or {}
        # MCP returns 'count' in metadata in some plugin versions
        if "count" in meta:
            return int(meta["count"])
        if isinstance(payload.get("data"), list):
            return len(payload["data"])
        if isinstance(payload, list):
            return len(payload)
    except Exception:
        pass
    return -1


def manifest_counts(manifest_path: str) -> tuple[int, int, int]:
    """(total entries, with_text, needs_text). 'with_text' = entries with size > 0."""
    with open(manifest_path) as f:
        m = json.load(f)
    total = len(m)
    with_text = sum(1 for e in m if (e.get("size") or 0) > 0)
    return total, with_text, total - with_text


def triage_counts(triage_path: str) -> tuple[int, int]:
    """(gross slice picks, unique items). Many papers double-count across slices."""
    with open(triage_path) as f:
        t = json.load(f)
    sel_groups = t.get("selected", [])
    unique_keys: set[str] = set()
    gross = 0
    for g in sel_groups:
        items = []
        if isinstance(g, dict):
            items = g.get("items") or g.get("selected") or []
        elif isinstance(g, list):
            items = g
        for it in items:
            gross += 1
            if isinstance(it, dict):
                k = it.get("key") or it.get("itemKey")
                if k:
                    unique_keys.add(k)
            elif isinstance(it, str):
                unique_keys.add(it)
    return gross, len(unique_keys)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("top", nargs="?", help="top-level Zotero collection key")
    ap.add_argument("selected", nargs="?", help="selected subcollection key")
    ap.add_argument("needs", nargs="?", help="needs-manual-access subcollection key")
    ap.add_argument("--discovery", help="path to discovery/<paper>/ folder (offline-friendly)")
    args = ap.parse_args()

    print("# PRISMA-flow counts")
    print()

    if args.discovery:
        d = os.path.abspath(args.discovery)
        if not os.path.isdir(d):
            sys.exit(f"discovery dir not found: {d}")
        # Find a text_manifest_*.json
        manifests = sorted(glob.glob(os.path.join(d, "text_manifest_*.json")))
        if manifests:
            mtot, mwt, mno = manifest_counts(manifests[0])
            print(f"text-manifest:         {os.path.basename(manifests[0])}")
            print(f"  manifest entries:    {mtot}")
            print(f"  with extracted text: {mwt}")
            print(f"  without text:        {mno}")
        else:
            print("(no text_manifest_*.json under discovery dir)")
        triage_path = os.path.join(d, "triage.json")
        if os.path.isfile(triage_path):
            gross, unique = triage_counts(triage_path)
            print()
            print("triage.json:")
            print(f"  gross slice picks:   {gross}")
            print(f"  unique items:        {unique}")
            if gross != unique:
                print(f"  dedup-collapse:      {gross - unique}  (slice overlap)")

    if args.top:
        if not os.environ.get("ZOTERO_MCP_TOKEN"):
            print("\n(skipping collection-size lookups — ZOTERO_MCP_TOKEN not set)")
            return
        mcp = MCP("flow", throttle=0.2)
        print()
        print("Zotero collection sizes:")
        for label, key in (("top",       args.top),
                           ("selected",  args.selected),
                           ("needs-MA",  args.needs)):
            if not key:
                continue
            n = collection_size(mcp, key)
            print(f"  {label:<10} {key}:  {n}")


if __name__ == "__main__":
    main()

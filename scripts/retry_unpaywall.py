#!/usr/bin/env python3
"""For each failed item in an acquire log (no PDF attached), query Unpaywall
for ALL OA locations (publisher + green repositories), try each `url_for_pdf`
until one yields a real PDF. Repository copies usually beat publisher landing
pages for automated fetch.

Usage:
  retry_unpaywall.py <acquire_log.json> [<out_log.json>]

Defaults out_log to <acquire_log>.retry.json if omitted.

Env:
  ZOTERO_MCP_TOKEN  required.
  ZOTERO_MCP_URL    optional MCP URL override.
  UNPAYWALL_EMAIL   polite-pool identifier (required by Unpaywall).
"""
import json, urllib.request, urllib.parse, os, sys

from zotero_mcp import MCP, result_json

EMAIL = os.environ.get("UNPAYWALL_EMAIL") or os.environ.get("OPENALEX_EMAIL") or os.environ.get("ZOTERO_USER_EMAIL")
if not EMAIL:
    sys.exit("set UNPAYWALL_EMAIL (or OPENALEX_EMAIL / ZOTERO_USER_EMAIL) — Unpaywall requires it")

def unpaywall(doi):
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}?email={EMAIL}"
    req = urllib.request.Request(url, headers={"User-Agent":f"zotero-setup/1.0 (mailto:{EMAIL})"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: retry_unpaywall.py <acquire_log.json> [<out_log.json>]")
    log_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else log_path.replace(".json", ".retry.json")
    log = json.load(open(log_path))
    mcp = MCP("retry", throttle=0.4)
    # Read parent item details once to get DOIs
    failed = [r for r in log if not r["result"].startswith("OK")]
    print(f"{len(failed)} failures to retry")
    updates = []
    for r in failed:
        key = r["key"]
        det = result_json(mcp.call("get_item_details", {"itemKey": key}, timeout=240))
        doi = (det or {}).get("data", det or {}).get("DOI") if det else None
        if not doi and det:
            doi = det.get("DOI") or ""
        if isinstance(det, dict) and "DOI" in det: doi = det["DOI"]
        # Fall back into the dict shape
        if not doi:
            doi = (det or {}).get("data",{}).get("DOI", "") if isinstance(det, dict) else ""
        if not doi:
            print(f"  {key}: no DOI in item — skip")
            updates.append({"key": key, "outcome": "no_doi"})
            continue
        u = unpaywall(doi.strip())
        if "error" in u:
            print(f"  {key}: Unpaywall error: {u['error'][:80]}")
            updates.append({"key": key, "outcome": f"unpaywall_error:{u['error'][:80]}"})
            continue
        # Build ordered list of candidate URLs: best_oa_location first, then all oa_locations.
        locs = []
        b = u.get("best_oa_location")
        if b and (b.get("url_for_pdf") or b.get("url")):
            locs.append(b)
        for L in u.get("oa_locations", []) or []:
            if L is not b: locs.append(L)
        if not locs:
            print(f"  {key}: Unpaywall says no OA locations  (is_oa={u.get('is_oa')})")
            updates.append({"key": key, "outcome": "no_oa", "is_oa": u.get("is_oa")})
            continue
        attempt_log = []
        attached = False
        for L in locs:
            url = L.get("url_for_pdf") or L.get("url")
            if not url: continue
            host = L.get("host_type","")
            ct = "application/pdf" if (url.endswith(".pdf") or "/pdf" in url) else None
            args = {"url": url, "parentItemKey": key, "ifExists": "skip"}
            if ct: args["contentType"] = ct
            r2 = mcp.call("import_attachment_url", args, timeout=240)
            if r2 and r2.get("error"):
                attempt_log.append({"url": url, "host": host, "outcome": f"net_err:{r2['error'].get('message','')[:60]}"})
                continue
            res = result_json(r2)
            ok = bool(res and res.get("success"))
            attempt_log.append({"url": url, "host": host, "outcome": "OK" if ok else f"FAIL:{(res or {}).get('error','?')}"})
            if ok:
                print(f"  {key}: attached from {host:11} {url[:80]}")
                attached = True
                break
        if not attached:
            print(f"  {key}: all {len(locs)} OA locations failed")
        updates.append({"key": key, "outcome": "OK" if attached else "all_failed", "attempts": attempt_log})
    with open(out_path,"w") as f:
        json.dump(updates, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}")
    n_ok = sum(1 for u in updates if u["outcome"]=="OK")
    print(f"{n_ok}/{len(updates)} now acquired via Unpaywall fallback")

if __name__=="__main__":
    main()

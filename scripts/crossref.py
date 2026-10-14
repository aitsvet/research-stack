"""Crossref REST client shared by discover.py, verify_refs.py, resolve_dois.py.

One polite-pool User-Agent, transient failures retried with backoff, a missing
DOI returned as None rather than raised.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.crossref.org/works"
EMAIL = os.environ.get("OPENALEX_EMAIL") or os.environ.get("ZOTERO_USER_EMAIL", "")


def user_agent(email=EMAIL):
    return f"research-stack/1.0 (mailto:{email})" if email else "research-stack/1.0"


UA = user_agent()


def _get(url, ua, timeout, attempts, delay):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    last_err = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)["message"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last_err = e
        except Exception as e:  # noqa: BLE001 — network errors are retried
            last_err = e
        time.sleep(delay)
        delay *= 2
    raise last_err if last_err else RuntimeError("crossref unreachable")


def work(doi, timeout=20, attempts=3, delay=1.0):
    """The work record for a DOI, None if Crossref does not know it."""
    url = f"{API}/{urllib.parse.quote(doi, safe='/.()')}"
    return _get(url, UA, timeout, attempts, delay)


def search(title, author=None, rows=5, mailto=EMAIL, timeout=45, attempts=3, delay=1.0):
    """Bibliographic title (+ author) search; the scored candidate items."""
    p = {"query.bibliographic": title, "rows": str(rows),
         "select": "DOI,title,author,issued,container-title,type,score"}
    if author:
        p["query.author"] = author
    if mailto:
        p["mailto"] = mailto
    msg = _get(API + "?" + urllib.parse.urlencode(p), user_agent(mailto),
               timeout, attempts, delay)
    return (msg or {}).get("items", [])

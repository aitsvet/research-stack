#!/usr/bin/env python3
"""Canonical Zotero MCP JSON-RPC client + generic helpers.

Import this instead of re-rolling the client. Domain/corpus specifics (paths,
item tables, site names) belong in the *project* repo, never in this module.

    from zotero_mcp import MCP, result_json, result_text, item_key
    m = MCP("myscript", throttle=0.2)
    r = m.call("create_item", {...})

Env: ZOTERO_MCP_TOKEN (required), ZOTERO_MCP_URL (optional).
"""
import json, urllib.request, urllib.error, os, time, html, re

MCP_URL = os.environ.get("ZOTERO_MCP_URL", "http://127.0.0.1:23120/mcp")
TOKEN   = os.environ.get("ZOTERO_MCP_TOKEN")

class MCP:
    def __init__(self, name="zotero_mcp", throttle=1.3):
        if not TOKEN: raise SystemExit("ZOTERO_MCP_TOKEN required")
        self.throttle = throttle
        b = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
            "protocolVersion":"2025-03-26","capabilities":{},
            "clientInfo":{"name":name,"version":"1"}}}).encode()
        with urllib.request.urlopen(self._req(b), timeout=30) as r:
            self.sid = r.headers["Mcp-Session-Id"]; r.read()
        self._raw({"jsonrpc":"2.0","method":"notifications/initialized"})
    def _req(self, body):
        h = {"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json",
             "Accept":"application/json, text/event-stream"}
        if getattr(self, "sid", None): h["Mcp-Session-Id"] = self.sid
        return urllib.request.Request(MCP_URL, data=body, method="POST", headers=h)
    def _raw(self, payload, timeout=180):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with urllib.request.urlopen(self._req(body), timeout=timeout) as r:
            data = r.read().decode("utf-8")
        if data.startswith("event:") or "\ndata:" in data:
            for ln in data.splitlines():
                if ln.startswith("data:"): return json.loads(ln[5:].strip())
            return None
        return json.loads(data) if data.strip() else None
    def call(self, tool, args, timeout=180):
        """tools/call with throttle, exponential back-off on HTTP 429 honouring
        Retry-After, and a non-fatal {"error": ...} result after repeated
        network failures (so batch loops can log the row and continue)."""
        delay = 2.0
        for attempt in range(7):
            time.sleep(self.throttle)
            try:
                return self._raw({"jsonrpc":"2.0","id":int(time.time()*1000)%10**8,
                    "method":"tools/call","params":{"name":tool,"arguments":args}}, timeout)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 6:
                    ra = e.headers.get("Retry-After")
                    time.sleep(max(float(ra) if ra else delay, delay)); delay = min(delay*2, 40); continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < 2:
                    time.sleep(delay); delay = min(delay*2, 40); continue
                return {"error": {"code": "network", "message": str(e)}}

def result_text(resp):
    try: return resp["result"]["content"][0]["text"]
    except Exception: return json.dumps(resp)[:200]

def result_json(resp):
    """Parse the JSON payload inside a tools/call text result; None if malformed."""
    try: return json.loads(resp["result"]["content"][0]["text"])
    except Exception: return None

def item_key(resp):
    t = result_text(resp)
    m = re.search(r'"(?:itemKey|key)"\s*:\s*"([A-Z0-9]{8})"', t)
    return m.group(1) if m else None

def add_note_file(mcp, parent, path, title, tags=None):
    """Attach a local text/markdown file as a child note (HTML-wrapped)."""
    body = open(path, encoding="utf-8").read()
    content = (f"<h1>{html.escape(title)}</h1>"
               f"<pre style=\"white-space:pre-wrap;font-family:monospace\">{html.escape(body)}</pre>")
    return mcp.call("add_note", {"itemKey":parent, "content":content, "tags":tags or []})

def import_file(mcp, parent, url, title, content_type=None, if_exists="add"):
    """import_attachment_url. NOTE: the URL must be PUBLIC (loopback/RFC-1918
    are rejected). if_exists='add' for many files→one parent, 'skip' to dedupe."""
    args = {"url":url, "parentItemKey":parent, "title":title, "ifExists":if_exists}
    if content_type: args["contentType"] = content_type
    return mcp.call("import_attachment_url", args)

def import_local_files(mcp, jobs, public_ip, port=28765,
                       content_type="application/pdf", if_exists="skip"):
    """Attach LOCAL files to Zotero items. import_attachment_url rejects
    literal loopback/RFC-1918 hosts, so this serves a throwaway copy of each
    file for the duration of the import, then tears the server down. A private
    IPv4 address is expressed through nip.io's DNS alias; it still resolves
    directly to the same host and no file is uploaded to that service.

        jobs : list of (local_path, parentItemKey, title)
        public_ip : a host IPv4 address reachable from the Zotero process
                    (`hostname -I`); private addresses are supported
    Returns [(parentItemKey, ok_bool, text)]. Serves a temp dir only (never the
    repo root) and uses the current Python interpreter.

    Staged names are forced to ASCII: the importer validates the URL string and
    rejects non-ASCII paths as "Invalid attachment URL", so a Cyrillic filename
    fails here even though everything else is correct. The original name is
    irrelevant downstream — Zotero titles the attachment from `title`."""
    import ipaddress as _ipaddress
    import sys as _sys
    import tempfile, shutil, subprocess, time as _t, os as _os, re as _re
    url_host = public_ip
    try:
        if _ipaddress.ip_address(public_ip).is_private:
            url_host = f"{public_ip}.nip.io"
    except ValueError:
        pass
    staging = tempfile.mkdtemp(prefix="zimp_")
    names = []
    for i, (path, _key, _title) in enumerate(jobs):
        ext = _os.path.splitext(path)[1]
        stem = _re.sub(r"[^A-Za-z0-9._-]+", "_",
                       _os.path.splitext(_os.path.basename(path))[0])
        nm = "f%d_%s%s" % (i, stem.strip("_")[:40] or "file", ext)
        shutil.copyfile(path, _os.path.join(staging, nm)); names.append(nm)
    srv = subprocess.Popen([_sys.executable, "-m", "http.server", str(port),
                            "--bind", "0.0.0.0", "--directory", staging],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _t.sleep(2); out = []
    try:
        for (path, key, title), nm in zip(jobs, names):
            url = "http://%s:%d/%s" % (url_host, port, nm)
            try:
                r = import_file(mcp, key, url, title, content_type, if_exists=if_exists)
                t = result_text(r); ok = ('"success": true' in t) or ("already" in t.lower())
            except Exception as e:
                t = str(e); ok = False
            out.append((key, ok, t[:120]))
    finally:
        srv.terminate(); srv.wait(); shutil.rmtree(staging, ignore_errors=True)
    return out

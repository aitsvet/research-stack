#!/usr/bin/env python3
# Minimal CDP driver for the already-running cookie'd chromium on :9222.
# Usage: cdp_eval.py nav <url>        # navigate current page, wait, print title
#        cdp_eval.py eval '<jsexpr>'  # eval JS in current page, print JSON result
# Importable: `from cdp_eval import Browser` gives the same connection to other scripts.
import os, sys, json, time, urllib.request, websocket

CDP = os.environ.get("CDP_URL", "http://127.0.0.1:9222")


class Browser:
    """Attach to the first page target, or with new_tab=True to a fresh tab of
    its own (closed again by close()) so the research tab is left alone."""
    def __init__(self, timeout=40, new_tab=False):
        self.target_id = None
        if new_tab:
            # data=b"" forces Content-Length: 0 — Chromium's DevTools endpoint
            # otherwise waits for a body on PUT and the request times out.
            page = json.load(urllib.request.urlopen(urllib.request.Request(
                CDP + "/json/new?about:blank", data=b"", method="PUT"), timeout=5))
            self.target_id = page["id"]
        else:
            d = json.load(urllib.request.urlopen(CDP + "/json", timeout=5))
            pages = [t for t in d if t["type"] == "page"]
            if not pages:
                sys.exit("no page target on %s — is chromium running?" % CDP)
            page = pages[0]
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                              max_size=None, timeout=timeout)
        self._id = 0
        self.cmd("Runtime.enable"); self.cmd("Page.enable")

    def cmd(self, method, **params):
        self._id += 1; mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid: return m

    def call(self, method, **params):
        """Like cmd(), but returns the result and raises on a protocol error."""
        m = self.cmd(method, **params)
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error']}")
        return m["result"]

    def evaluate(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("result", {})

    def js(self, expr):
        return self.evaluate(expr).get("value")

    def close(self):
        try:
            if self.target_id:
                urllib.request.urlopen(CDP + "/json/close/" + self.target_id, timeout=5)
        finally:
            self.ws.close()


if __name__ == "__main__":
    br = Browser()
    mode = sys.argv[1]
    if mode == "nav":
        br.cmd("Page.navigate", url=sys.argv[2])
        time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 8)
        print("TITLE:", br.js("document.title"))
    elif mode == "eval":
        res = br.evaluate(sys.argv[2])
        print(json.dumps(res.get("value", res), ensure_ascii=False)[:6000])
    br.close()

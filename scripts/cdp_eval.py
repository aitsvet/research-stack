#!/usr/bin/env python3
# Minimal CDP driver for the already-running cookie'd chromium on :9222.
# Usage: cdp_eval.py nav <url>        # navigate current page, wait, print title
#        cdp_eval.py eval '<jsexpr>'  # eval JS in current page, print JSON result
# Importable: `from cdp_eval import Browser` gives the same connection to other scripts.
import os, sys, json, time, urllib.request, websocket

CDP = os.environ.get("CDP_URL", "http://127.0.0.1:9222")


class Browser:
    def __init__(self, timeout=40):
        d = json.load(urllib.request.urlopen(CDP + "/json", timeout=5))
        pages = [t for t in d if t["type"] == "page"]
        if not pages:
            sys.exit("no page target on %s — is chromium running?" % CDP)
        self.ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                              max_size=None, timeout=timeout)
        self._id = 0
        self.cmd("Runtime.enable"); self.cmd("Page.enable")

    def cmd(self, method, **params):
        self._id += 1; mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid: return m

    def evaluate(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("result", {})

    def js(self, expr):
        return self.evaluate(expr).get("value")

    def close(self):
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

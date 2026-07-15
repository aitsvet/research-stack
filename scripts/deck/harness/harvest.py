#!/usr/bin/env python3
"""Harvest browser-measured slide geometry into JSON for emit_pptx.py.

The layout is authored as HTML (see tokens.css) and laid out by the zotero
container's Chromium — a real engine measures the text and aligns the boxes.
Sibling of scripts/cdp_eval.py, same CDP-on-:9222 infra, but without output
truncation and with its own page (does not disturb the research tab).

The HTML must live under zotero-setup/config/ (mounted as /config in the
container), so pass container paths:

  harvest.py file:///config/deck-harness/sample.html out.json [--shot page.png]

Every element to be exported carries data-pptx="text|card|image" (in z-order,
cards before their text); images also carry data-src="<path for emitter>".
Slides are elements with class "slide".
"""
import argparse
import base64
import json
import time
import urllib.request

import websocket

HARVEST_JS = r"""
(() => {
  const slides = [...document.querySelectorAll('.slide')].map(slide => {
    const s = slide.getBoundingClientRect();
    const cs = getComputedStyle(slide);
    return {
      w: s.width, h: s.height, bg: cs.backgroundColor,
      els: [...slide.querySelectorAll('[data-pptx]')].map(el => {
        const r = el.getBoundingClientRect();
        const c = getComputedStyle(el);
        return {
          kind: el.dataset.pptx,
          x: r.x - s.x, y: r.y - s.y, w: r.width, h: r.height,
          text: el.innerText || "",
          size: parseFloat(c.fontSize), weight: c.fontWeight,
          color: c.color, bgColor: c.backgroundColor,
          radius: parseFloat(c.borderRadius) || 0,
          align: c.textAlign,
          lineHeight: parseFloat(c.lineHeight) / parseFloat(c.fontSize),
          src: el.dataset.src || null,
        };
      })
    };
  });
  return JSON.stringify(slides);
})()
"""


class CDP:
    def __init__(self, port=9222):
        self.port = port
        self._id = 0
        # data=b"" forces Content-Length: 0 — Chromium's DevTools endpoint
        # otherwise waits for a body on PUT and the request times out.
        new = json.load(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?about:blank", data=b"", method="PUT"),
            timeout=5))
        self.target_id = new["id"]
        self.ws = websocket.create_connection(
            new["webSocketDebuggerUrl"], max_size=None, timeout=60)

    def cmd(self, method, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._id:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m["result"]

    def close(self):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json/close/{self.target_id}", timeout=5)
        finally:
            self.ws.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="container-visible URL, e.g. file:///config/deck-harness/x.html")
    ap.add_argument("out", help="geometry JSON path")
    ap.add_argument("--shot", help="also save a full-page screenshot (reference render)")
    ap.add_argument("--wait", type=float, default=2.0)
    a = ap.parse_args()

    cdp = CDP()
    try:
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Page.navigate", url=a.url)
        time.sleep(a.wait)  # file:// + local fonts settle fast
        r = cdp.cmd("Runtime.evaluate", expression=HARVEST_JS, returnByValue=True)
        slides = json.loads(r["result"]["value"])
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(slides, f, ensure_ascii=False, indent=1)
        print(f"harvested {len(slides)} slide(s), "
              f"{sum(len(s['els']) for s in slides)} elements -> {a.out}")
        if a.shot:
            first = slides[0]
            cdp.cmd("Emulation.setDeviceMetricsOverride", width=int(first["w"]),
                    height=int(first["h"]), deviceScaleFactor=1, mobile=False)
            shot = cdp.cmd("Page.captureScreenshot", format="png")
            with open(a.shot, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            print(f"screenshot -> {a.shot}")
    finally:
        cdp.close()


if __name__ == "__main__":
    main()

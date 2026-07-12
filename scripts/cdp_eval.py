#!/usr/bin/env python3
# Minimal CDP driver for the already-running cookie'd chromium on :9222.
# Usage: cdp_eval.py nav <url>        # navigate current page, wait, print title
#        cdp_eval.py eval '<jsexpr>'  # eval JS in current page, print JSON result
import sys, json, time, urllib.request, websocket

def target():
    d = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5))
    pages=[t for t in d if t["type"]=="page"]
    return pages[0]["webSocketDebuggerUrl"]

ws = websocket.create_connection(target(), max_size=None, timeout=40)
_id=0
def cmd(method, **params):
    global _id; _id+=1; mid=_id
    ws.send(json.dumps({"id":mid,"method":method,"params":params}))
    while True:
        m=json.loads(ws.recv())
        if m.get("id")==mid: return m

cmd("Runtime.enable"); cmd("Page.enable")
mode=sys.argv[1]
if mode=="nav":
    cmd("Page.navigate", url=sys.argv[2])
    time.sleep(float(sys.argv[3]) if len(sys.argv)>3 else 8)
    r=cmd("Runtime.evaluate", expression="document.title", returnByValue=True)
    print("TITLE:", r["result"]["result"].get("value"))
elif mode=="eval":
    r=cmd("Runtime.evaluate", expression=sys.argv[2], returnByValue=True, awaitPromise=True)
    res=r.get("result",{}).get("result",{})
    print(json.dumps(res.get("value", res), ensure_ascii=False)[:6000])
ws.close()

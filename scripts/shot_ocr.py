#!/usr/bin/env python3
"""Screenshot a screen region, OCR it, and answer from a corpus index.

The loop an agent needs for "the question is somewhere on my screen": grab the
region, read its text with tesseract, hand that text to `corpus_index.py search`,
print the answer material with source citations.

    shot_ocr.py capture [--region X,Y,W,H] [--out shot.png] [--backend auto]
    shot_ocr.py ocr     <image> [--lang rus+eng] [--psm 6] [--region X,Y,W,H]
    shot_ocr.py ask     [--region X,Y,W,H] [--image PATH] [--primary KEY] [--k 3]
                        [--db PATH] [--lang rus+eng] [--keep]

`ask` is the auto mode: with no `--image` it captures the region, and it prints
a single prompt-ready block — the OCR text, then the retrieved chunks with
`path#page` citations.

OCR engine (`--ocr`): `tesseract` (default; local CPU, ~1 s, best on clean
on-screen text), `chandra` (a VLM on an OpenAI-compatible endpoint, `CHANDRA_URL`;
better on photographed book pages, complex layouts and Arabic, but seconds per
image, needs a GPU box, and it can substitute a word that looks alike — which in
a retrieval query changes the meaning, so prefer it only when tesseract's text is
visibly wrong), or `auto` (tesseract when installed, else chandra). The
screenshot needs one of grim (Wayland), maim/scrot/gnome-screenshot (X11) or
ImageMagick `import`.

Region defaults to the whole screen. `capture` picks the first backend that
exists; `--backend` forces one.
"""
import argparse, os, re, shutil, subprocess, sys, tempfile

def have(cmd):
    return shutil.which(cmd) is not None

def parse_region(s):
    if not s:
        return None
    try:
        x, y, w, h = [int(v) for v in s.replace(" ", "").split(",")]
        return (x, y, w, h)
    except Exception:
        sys.exit(f"--region wants X,Y,W,H (got {s!r})")

def capture(path, region=None, backend="auto"):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    geom = "%d,%d %dx%d" % region if region else None
    crop = ""
    # grim wants a geometry string; maim/scrot/import want x,y,w,h or a crop afterwards
    if backend in ("auto", "grim") and have("grim"):
        cmd = ["grim"] + (["-g", geom] if geom else []) + [path]
    elif backend in ("auto", "maim") and have("maim"):
        cmd = ["maim"] + (["-g", "%d,%d,%d,%d" % region] if region else []) + [path]
    elif backend in ("auto", "scrot") and have("scrot"):
        cmd = ["scrot", path]
        crop = "scrot"
    elif backend in ("auto", "gnome") and have("gnome-screenshot"):
        cmd = ["gnome-screenshot", "-f", path]
        crop = "gnome"
    elif backend in ("auto", "magick") and (have("import") or have("magick")):
        cmd = (["import", "-window", "root", path] if have("import")
               else ["magick", "import", "-window", "root", path])
        crop = "magick"
    else:
        sys.exit("no screenshot tool found: install grim (Wayland) or maim/scrot (X11)")
    rc = subprocess.call(cmd)
    if rc != 0:
        sys.exit(f"{cmd[0]} failed with {rc}")
    if crop and region:
        from PIL import Image
        im = Image.open(path)
        x, y, w, h = region
        im.crop((x, y, x + w, y + h)).save(path)
    return path

CHANDRA_PROMPT = (
    "OCR this image to HTML. Only use these tags ['math','br','i','b','u','del',"
    "'sup','sub','table','tr','td','p','th','div','pre','h1','h2','h3','h4','h5',"
    "'ul','ol','li','a','span','hr','img','strong','small','caption']. Keep the "
    "reading order, do not translate, do not summarise, drop nothing.")

def _html_text(html):
    """Chandra answers HTML; keep its paragraph structure, drop the tags."""
    import html as _html
    txt = re.sub(r"<\s*(br|/p|/div|/h[1-6]|/li|/tr)\s*/?>", "\n", html, flags=re.I)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = _html.unescape(txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()

def ocr_chandra(path):
    """One image through a vLLM-hosted Chandra (same endpoint as chandra_ocr.py)."""
    import base64, json, os, urllib.request, urllib.error
    url = os.environ.get("CHANDRA_URL", "http://localhost:8000/v1").rstrip("/")
    model = os.environ.get("CHANDRA_MODEL", "chandra")
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    body = {"model": model, "temperature": 0, "top_p": 0.1, "max_tokens": 4000,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": CHANDRA_PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]}
    req = urllib.request.Request(url + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        sys.exit(f"chandra at {url} failed: {type(e).__name__}: {e}")
    return _html_text(data["choices"][0]["message"]["content"])

def ocr(path, lang="rus+eng", psm=6, engine="tesseract"):
    if engine == "chandra" or (engine == "auto" and not have("tesseract")):
        return ocr_chandra(path)
    if not have("tesseract"):
        sys.exit("tesseract not installed (apt install tesseract-ocr tesseract-ocr-rus) "
                 "— or pass --ocr chandra with CHANDRA_URL set")
    out = subprocess.run(["tesseract", path, "stdout", "-l", lang, "--psm", str(psm)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("tesseract failed: " + out.stderr.strip()[:300])
    return out.stdout

def search(text, db, primary, k, extra=None):
    here = os.path.dirname(os.path.abspath(__file__))
    tool = os.path.join(here, "corpus_index.py")
    if not os.path.exists(tool):
        tool = os.path.expanduser("~/research-stack/scripts/corpus_index.py")
    q = " ".join(text.split())[:400] or " "
    cmd = [sys.executable, tool, "search", q, "-k", str(k), "--context"]
    if db:
        cmd += ["--db", db]
    if primary:
        cmd += ["--primary", primary]
    if extra:
        cmd += extra
    return subprocess.run(cmd, capture_output=True, text=True)

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture"); c.add_argument("--region")
    c.add_argument("--out", default="/tmp/shot.png"); c.add_argument("--backend", default="auto")
    o = sub.add_parser("ocr"); o.add_argument("image"); o.add_argument("--lang", default="rus+eng")
    o.add_argument("--psm", type=int, default=6); o.add_argument("--region")
    o.add_argument("--ocr", default="tesseract", choices=["tesseract", "chandra", "auto"])
    a = sub.add_parser("ask"); a.add_argument("--region"); a.add_argument("--image")
    a.add_argument("--primary"); a.add_argument("-k", "--k", type=int, default=3)
    a.add_argument("--db"); a.add_argument("--lang", default="rus+eng")
    a.add_argument("--ocr", default="tesseract", choices=["tesseract", "chandra", "auto"])
    a.add_argument("--keep", action="store_true", help="keep the screenshot")
    args = ap.parse_args(argv)

    if args.cmd == "capture":
        print(capture(args.out, parse_region(args.region)))
        return

    if args.cmd == "ocr":
        path = args.image
        region = parse_region(args.region)
        if region:
            from PIL import Image
            im = Image.open(path); x, y, w, h = region
            tmp = path + ".crop.png"
            im.crop((x, y, x + w, y + h)).save(tmp); path = tmp
        print(ocr(path, args.lang, args.psm, args.ocr))
        return

    # ask
    shot = args.image
    if not shot:
        shot = os.path.join(tempfile.gettempdir(), "shot_%d.png" % os.getpid())
        capture(shot, parse_region(args.region))
    text = ocr(shot, args.lang, engine=args.ocr)
    if not text.strip():
        print("OCR returned no text — check the region and --lang", file=sys.stderr)
    print("=== OCR (" + os.path.basename(shot) + ") ===")
    print(text.strip()[:1500])
    print("\n=== ANSWER MATERIAL ===")
    r = search(text, args.db, args.primary, args.k)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
    if not args.keep and not args.image:
        try:
            os.unlink(shot)
        except OSError:
            pass

if __name__ == "__main__":
    main(sys.argv[1:])

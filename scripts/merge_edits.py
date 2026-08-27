#!/usr/bin/env python3
"""Reconcile an editor's hand-edited copy of an exported document with the Markdown master.

The master is the only file anyone edits — except that the editor does not edit it. They
get an exported `.docx`/`.doc`, mark it up by hand and send it back, and by then the master
has usually moved too. That is a three-way merge, and skimming a diff loses paragraphs
silently.

    merge_edits.py PAPER.md --sent <git-rev|file> --returned EDITED.docx [--apply]

Three sides, aligned paragraph by paragraph:

  * **sent** — the revision the editor actually received. Give a git revision (resolved as
    ``<rev>:<path>`` in the master's repository) rather than trusting memory of what went out.
  * **returned** — their copy. ``.docx`` is read directly, ``.doc``/``.odt``/``.rtf`` go through
    ``convert_office.py`` (LibreOffice in the ``docconv`` service), ``.md``/``.txt`` are read as is.
  * **master** — the file as it stands now.

Citation markers are blanked before comparing, because one renumbering pass otherwise marks
every paragraph as changed and buries the real edits. Pass ``--keep-refs`` when the numbering
itself is what you are checking.

Each paragraph of the sent version comes back with a verdict:

    ACCEPT    only the editor moved      -> take their wording verbatim
    MINE      only the master moved      -> their copy is simply stale here
    CONVERGED both moved to the same text
    CONFLICT  both moved, differently    -> reconcile by hand, keep both intents
    DROPPED   the editor deleted it
    ADDED     the editor wrote a paragraph that was not in the sent version
    NEW       the master grew a paragraph after the file went out

Front matter is the one place where the classes lie: an exporter that turns adviser lines into a
footnote and strips the ``**`` off a label leaves them showing as DROPPED and ACCEPT against the
Markdown. Read those two classes with the export's own conventions in mind.

``--apply`` writes back only the ACCEPT class, and only where the master line is unambiguous:
plain body paragraphs, matched exactly once, no headings and no emphasis-wrapped lines. Every
other class is a decision, and decisions are not automated. What is not carried across has to
be named with its reason in the reply — a paragraph dropped in silence is the one failure the
editor cannot see.
"""
import argparse
import difflib
import os
import re
import subprocess
import sys
import tempfile
import zipfile

REF = re.compile(r"\[\d+(?:\s*[;,–—-]\s*\d+)*\]")
WS = re.compile(r"\s+")
HEADING = re.compile(r"^(#+\s*)(.*)$")
EMPH = re.compile(r"^(\*{1,2})(.+?)(\*{1,2})$")
IMAGE = re.compile(r"^!\[")


class Para:
    """One paragraph, with enough context to write it back into its source line."""

    def __init__(self, text, line=None, prefix="", suffix="", plain=True):
        self.text = text
        self.line = line
        self.prefix = prefix
        self.suffix = suffix
        self.plain = plain

    def key(self, keep_refs=False):
        t = self.text if keep_refs else REF.sub("[]", self.text)
        return WS.sub(" ", t).strip()


def from_markdown(text):
    out = []
    for i, raw in enumerate(text.replace("﻿", "").split("\n")):
        s = raw.strip()
        if not s or IMAGE.match(s):
            continue
        prefix, suffix, plain = "", "", True
        m = HEADING.match(s)
        if m:
            prefix, s, plain = m.group(1), m.group(2).strip(), False
        m = EMPH.match(s)
        if m and m.group(1) == m.group(3):
            prefix, s, suffix, plain = prefix + m.group(1), m.group(2).strip(), m.group(3), False
        if s:
            out.append(Para(s, line=i, prefix=prefix, suffix=suffix, plain=plain))
    return out


def from_docx(path):
    """Paragraph text straight out of the package — no LibreOffice round trip needed."""
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        t = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
        t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
        if t:
            out.append(Para(t))
    return out


def from_office(path, stack):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, os.path.join(stack, "scripts", "convert_office.py"),
                        path, "--out", tmp, "--force"], check=True,
                       stdout=subprocess.DEVNULL)
        md = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".md")
        if not os.path.exists(md):
            sys.exit(f"convert_office.py produced no markdown for {path}")
        with open(md, encoding="utf-8") as f:
            return from_markdown(f.read())


def load(path, stack):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return from_docx(path)
    if ext in (".doc", ".odt", ".rtf"):
        return from_office(path, stack)
    with open(path, encoding="utf-8", errors="replace") as f:
        return from_markdown(f.read())


def load_sent(spec, master_path, stack):
    """A git revision of the master, or a standalone file."""
    if os.path.exists(spec):
        return load(spec, stack)
    repo = os.path.dirname(os.path.abspath(master_path))
    if ":" in spec:
        rev = spec
    else:
        top = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"],
                             capture_output=True).stdout.decode().strip()
        if not top:
            sys.exit(f"--sent: {master_path} is not in a git repository, pass a file instead")
        rev = f"{spec}:{os.path.relpath(os.path.abspath(master_path), top)}"
    try:
        blob = subprocess.run(["git", "-C", repo, "show", rev], check=True,
                              capture_output=True).stdout.decode("utf-8")
    except subprocess.CalledProcessError:
        sys.exit(f"--sent: neither a file nor a resolvable git revision: {spec}")
    return from_markdown(blob)


def align(a, b, keep_refs, floor):
    """Pair up two paragraph lists. Returns [(i|None, j|None), …] in reading order.

    Equal runs pair positionally; a replaced run pairs greedily by similarity so a lightly
    edited paragraph stays matched to its original instead of reading as delete + insert.
    """
    ka = [p.key(keep_refs) for p in a]
    kb = [p.key(keep_refs) for p in b]
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs += [(i1 + n, j1 + n) for n in range(i2 - i1)]
            continue
        left, right = list(range(i1, i2)), list(range(j1, j2))
        while left and right:
            best, score = None, floor
            for i in left:
                for j in right:
                    r = difflib.SequenceMatcher(None, ka[i], kb[j], autojunk=False).ratio()
                    if r >= score:
                        best, score = (i, j), r
            if not best:
                break
            pairs.append(best)
            left.remove(best[0])
            right.remove(best[1])
        pairs += [(i, None) for i in left]
        pairs += [(None, j) for j in right]
    pairs.sort(key=lambda p: (p[0] if p[0] is not None else 1e9, p[1] if p[1] is not None else 1e9))
    return pairs


def cut(s, n):
    return s if len(s) <= n else s[:n - 1] + "…"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("master", help="the Markdown master")
    ap.add_argument("--sent", required=True, metavar="REV|FILE",
                    help="the revision the editor received (git rev of the master, or a file)")
    ap.add_argument("--returned", required=True, metavar="FILE",
                    help="the editor's copy: .docx / .doc / .odt / .rtf / .md / .txt")
    ap.add_argument("--keep-refs", action="store_true",
                    help="compare citation markers too (default: blank them)")
    ap.add_argument("--similarity", type=float, default=0.55,
                    help="floor for pairing an edited paragraph to its original (default 0.55)")
    ap.add_argument("--width", type=int, default=300, help="report excerpt width")
    ap.add_argument("--apply", action="store_true",
                    help="write ACCEPT paragraphs into the master (unambiguous plain lines only)")
    a = ap.parse_args()

    stack = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    master = load(a.master, stack)
    sent = load_sent(a.sent, a.master, stack)
    returned = load(a.returned, stack)

    s2r = dict(align(sent, returned, a.keep_refs, a.similarity))
    s2m = dict(align(sent, master, a.keep_refs, a.similarity))
    matched_r = {j for j in s2r.values() if j is not None}
    matched_m = {j for j in s2m.values() if j is not None}

    verdicts = []
    for i, p in enumerate(sent):
        j, k = s2r.get(i), s2m.get(i)
        r = returned[j] if j is not None else None
        m = master[k] if k is not None else None
        base = p.key(a.keep_refs)
        theirs = r.key(a.keep_refs) if r else None
        mine = m.key(a.keep_refs) if m else None
        if theirs == base and mine == base:
            continue
        if r is None:
            verdicts.append(("DROPPED", i, p, r, m))
        elif mine == base or m is None:
            verdicts.append(("ACCEPT", i, p, r, m))
        elif theirs == base:
            verdicts.append(("MINE", i, p, r, m))
        elif theirs == mine:
            verdicts.append(("CONVERGED", i, p, r, m))
        else:
            verdicts.append(("CONFLICT", i, p, r, m))
    for j, r in enumerate(returned):
        if j not in matched_r:
            verdicts.append(("ADDED", None, None, r, None))
    for k, m in enumerate(master):
        if k not in matched_m:
            verdicts.append(("NEW", None, None, None, m))

    order = ["CONFLICT", "ACCEPT", "DROPPED", "ADDED", "MINE", "CONVERGED", "NEW"]
    print(f"sent {len(sent)} paragraphs, returned {len(returned)}, master {len(master)}"
          f"{'' if a.keep_refs else '  (citation markers blanked for comparison)'}\n")
    for tag in order:
        items = [v for v in verdicts if v[0] == tag]
        if not items:
            continue
        print(f"## {tag}  ({len(items)})")
        for _, i, p, r, m in items:
            print()
            if p is not None:
                print(f"  sent     {cut(p.text, a.width)}")
            if r is not None and (p is None or r.key(a.keep_refs) != p.key(a.keep_refs)):
                print(f"  editor   {cut(r.text, a.width)}")
            if m is not None and (p is None or m.key(a.keep_refs) != p.key(a.keep_refs)):
                print(f"  master   {cut(m.text, a.width)}")
        print()

    if not a.apply:
        counts = ", ".join(f"{t} {len([v for v in verdicts if v[0] == t])}"
                           for t in order if any(v[0] == t for v in verdicts))
        print(f"totals: {counts or 'no differences'}")
        print("\nRe-run with --apply to take the ACCEPT class verbatim; every other class is a "
              "decision. Name in your reply what you did not carry across, and why.")
        return

    with open(a.master, encoding="utf-8") as f:
        lines = f.read().split("\n")
    written, skipped = 0, []
    for tag, i, p, r, m in verdicts:
        if tag != "ACCEPT" or m is None:
            continue
        if not m.plain:
            skipped.append((m.text, "not a plain body paragraph"))
            continue
        hits = [n for n, ln in enumerate(lines) if ln.strip() == (m.prefix + m.text + m.suffix)]
        if len(hits) != 1:
            skipped.append((m.text, f"{len(hits)} matching lines in the master"))
            continue
        lines[hits[0]] = m.prefix + r.text + m.suffix
        written += 1
    with open(a.master, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"applied {written} paragraph(s) verbatim to {a.master}")
    for text, why in skipped:
        print(f"  SKIPPED ({why}): {cut(text, 120)}")
    if skipped:
        print("Skipped paragraphs stay yours to place by hand.")


if __name__ == "__main__":
    main()

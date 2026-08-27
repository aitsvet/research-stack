#!/usr/bin/env python3
"""Mechanical prose checks for a long-form draft, so the same sweep is not re-run by eye.

Editing a paper means the same reading passes over and over: is any thesis stated twice, did a
banned tic creep back, did a paragraph swell past what a reader carries, did a sentence collapse
into a telegraphic subject–predicate stub. All of it is countable, and counting it leaves attention
for what is not.

    prose_lint.py PAPER.md [--config prose_lint.json] [--strict] [--json]

The checks are language- and domain-neutral; every threshold and every word list lives in the
config, which belongs to the project, not here. With no config the structural checks still run on
their defaults and the vocabulary checks stay silent.

    banned        words and phrases the project has ruled out, with the reason printed
    repeats       word n-grams recurring above a threshold — stock formulations
    duplicates    sentence pairs above a similarity floor — the same thesis stated twice
    paragraphs    length distribution against a band, plus a hard ceiling
    sentences     words per sentence against a band
    stubs         paragraph-opening and paragraph-closing sentences under N words
    punctuation   colon and dash density per hundred paragraphs
    citations     the same [n] repeated inside a short window of paragraphs
    glossing      a listed term whose first use carries no parenthetical explanation

``stop_at`` ends the body at the bibliography and ``skip`` drops front-matter label lines; both are
regexes in the config, because where the prose starts and stops is a house convention.

Findings are advisory: the exit status is 0 unless --strict. A finding is a place to look, not a
verdict — a legitimate short closing sentence and a telegraphic stub count the same here.
"""
import argparse
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_edits import from_markdown  # noqa: E402  (paragraph extraction, one implementation)

DEFAULTS = {
    "banned": {},
    "para_mean": [380, 900],
    "para_max": 1250,
    "sentence_mean": [17.0, 24.0],
    "punct_per_100_paras": 60,
    "stub_words": 9,
    "ngram": 5,
    "ngram_min": 3,
    "dup_ratio": 0.72,
    "dup_min_words": 8,
    "ref_window": 2,
    "gloss": [],
    "gloss_chars": 140,
    "stop_at": r"(?i)^(#+\s*)?(\*+)?\s*(библиограф|список литературы|references|bibliograph)",
    "skip": r"(?i)^(\*+)?\s*(УДК|UDC|Ключевые слова|Keywords|Аннотация|Abstract|Резюме|Summary"
            r"|Научный руководитель|Scientific adviser)",
    "abbrev": ["ст", "стт", "п", "пп", "ч", "гл", "рис", "табл", "см", "др", "т", "г", "гг",
               "руб", "тыс", "млн", "млрд", "изд", "ред", "вып", "с", "напр",
               "no", "vol", "pp", "ed", "eds", "cf", "fig", "art", "ch", "e.g", "i.e"],
}

WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
REF = re.compile(r"\[(\d+(?:\s*[;,]\s*\d+)*)\]")
DASH = re.compile(r"(?<=\s)[—–](?=\s)")
SENT_END = re.compile(r"(?<=[.!?…])\s+(?=[«\"'(\[]?[^\W\d_])", re.UNICODE)


def sentences(text, abbrev):
    """Split into sentences without breaking on «ст. 251» or «А.И. Иванов»."""
    out, buf = [], ""
    for piece in SENT_END.split(text):
        buf = (buf + " " + piece).strip() if buf else piece
        tail = buf.rstrip()
        last = WORD.findall(tail[-12:])
        if tail.endswith(".") and last and (last[-1].lower() in abbrev or len(last[-1]) == 1):
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return [s for s in out if s.strip()]


def norm_words(text):
    return [w.lower() for w in WORD.findall(text)]


def check_banned(paras, cfg, add):
    for term, why in cfg["banned"].items():
        pat = re.compile(term if term.startswith("(") else r"(?<![^\W\d_])" + re.escape(term) +
                         r"(?![^\W\d_])", re.I | re.UNICODE)
        hits = [(p, m) for p in paras for m in pat.finditer(p.text)]
        if hits:
            add("banned", f"«{term}» × {len(hits)} — {why}",
                [ctx(p.text, m.start()) for p, m in hits[:6]])


def ctx(text, i, span=46):
    return "…" + text[max(0, i - span):i + span].strip() + "…"


def check_repeats(paras, cfg, add):
    n, floor = cfg["ngram"], cfg["ngram_min"]
    seen = {}
    for p in paras:
        w = norm_words(p.text)
        for k in range(len(w) - n + 1):
            seen.setdefault(" ".join(w[k:k + n]), []).append(p)
    found = {g: ps for g, ps in seen.items() if len(ps) >= floor}
    keep = [g for g in found if not any(g != o and g in o for o in found)]
    for g in sorted(keep, key=lambda g: -len(found[g])):
        add("repeats", f"«{g}» × {len(found[g])}", [])


def check_duplicates(sents, cfg, add):
    floor, minw = cfg["dup_ratio"], cfg["dup_min_words"]
    keys = [(s, " ".join(norm_words(s))) for s in sents]
    keys = [(s, k) for s, k in keys if len(k.split()) >= minw]
    for i in range(len(keys)):
        a_s, a_k = keys[i]
        for j in range(i + 1, len(keys)):
            b_s, b_k = keys[j]
            if abs(len(a_k) - len(b_k)) > len(a_k) * 0.5:
                continue
            m = difflib.SequenceMatcher(None, a_k, b_k, autojunk=False)
            if m.real_quick_ratio() < floor or m.quick_ratio() < floor:
                continue
            r = m.ratio()
            if r >= floor:
                add("duplicates", f"similarity {r:.2f}", [a_s[:200], b_s[:200]])


def check_shape(paras, sents, cfg, add):
    lens = sorted(len(p.text) for p in paras)
    if lens:
        mean = sum(lens) / len(lens)
        lo, hi = cfg["para_mean"]
        band = "" if lo <= mean <= hi else f"  OUTSIDE BAND {lo}–{hi}"
        add("paragraphs", f"{len(lens)} paragraphs, mean {mean:.0f} chars, "
                          f"median {lens[len(lens)//2]}, max {lens[-1]}{band}", [])
        over = [p for p in paras if len(p.text) > cfg["para_max"]]
        for p in over:
            add("paragraphs", f"{len(p.text)} chars > {cfg['para_max']}", [p.text[:160] + "…"])
    if sents:
        counts = [len(norm_words(s)) for s in sents]
        mean = sum(counts) / len(counts)
        lo, hi = cfg["sentence_mean"]
        band = "" if lo <= mean <= hi else f"  OUTSIDE BAND {lo}–{hi}"
        add("sentences", f"{len(counts)} sentences, mean {mean:.1f} words{band}", [])


def check_stubs(paras, cfg, add):
    limit = cfg["stub_words"]
    for p in paras:
        ss = sentences(p.text, set(cfg["abbrev"]))
        if len(ss) < 2:
            continue
        if len(norm_words(ss[0])) <= limit:
            add("stubs", f"opener, {len(norm_words(ss[0]))} words", [ss[0]])
        if len(norm_words(ss[-1])) <= limit:
            add("stubs", f"closer, {len(norm_words(ss[-1]))} words", [ss[-1]])


def check_punctuation(paras, cfg, add):
    colons = sum(t.count(":") for t in (p.text for p in paras))
    dashes = sum(len(DASH.findall(p.text)) for p in paras)
    per100 = (colons + dashes) * 100.0 / max(len(paras), 1)
    flag = "" if per100 <= cfg["punct_per_100_paras"] else f"  OVER {cfg['punct_per_100_paras']}"
    add("punctuation", f"{colons} colons + {dashes} dashes = {per100:.0f} per 100 paragraphs{flag}", [])


def check_citations(paras, cfg, add):
    w = cfg["ref_window"]
    for i, p in enumerate(paras):
        here = {n.strip() for m in REF.finditer(p.text) for n in m.group(1).split(";")}
        for j in range(i + 1, min(i + 1 + w, len(paras))):
            nxt = {n.strip() for m in REF.finditer(paras[j].text) for n in m.group(1).split(";")}
            for n in sorted(here & nxt):
                add("citations", f"[{n}] repeats {j - i} paragraph(s) later",
                    [ctx(p.text, p.text.find(f"[{n}")), ctx(paras[j].text, paras[j].text.find(f"[{n}"))])


def check_glossing(paras, cfg, add):
    body = "\n".join(p.text for p in paras)
    for term in cfg["gloss"]:
        m = re.search(r"(?<![^\W\d_])" + re.escape(term), body, re.I | re.UNICODE)
        if not m:
            continue
        half = cfg["gloss_chars"] // 2
        window = body[max(0, m.start() - half):m.end() + half]
        if "(" not in window and ")" not in window:
            add("glossing", f"«{term}» first use carries no parenthetical", [ctx(body, m.start())])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft")
    ap.add_argument("--config", help="JSON overriding any default (see the module docstring)")
    ap.add_argument("--only", action="append", metavar="CHECK", help="run only these checks")
    ap.add_argument("--strict", action="store_true", help="exit 1 when anything is reported")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    cfg = dict(DEFAULTS)
    if a.config:
        with open(a.config, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["abbrev"] = {x.lower() for x in cfg["abbrev"]}

    with open(a.draft, encoding="utf-8") as f:
        paras = from_markdown(f.read())
    stop = re.compile(cfg["stop_at"])
    body = []
    for p in paras:
        if stop.match(p.prefix + p.text):
            break
        body.append(p)
    skip = re.compile(cfg["skip"])
    prose = [p for p in body if p.plain and not skip.match(p.prefix + p.text)]
    sents = [s for p in prose for s in sentences(p.text, cfg["abbrev"])]

    findings = []

    def add(check, headline, excerpts):
        findings.append({"check": check, "headline": headline, "excerpts": excerpts})

    checks = {
        "banned": lambda: check_banned(prose, cfg, add),
        "repeats": lambda: check_repeats(prose, cfg, add),
        "duplicates": lambda: check_duplicates(sents, cfg, add),
        "shape": lambda: check_shape(prose, sents, cfg, add),
        "stubs": lambda: check_stubs(prose, cfg, add),
        "punctuation": lambda: check_punctuation(prose, cfg, add),
        "citations": lambda: check_citations(prose, cfg, add),
        "glossing": lambda: check_glossing(prose, cfg, add),
    }
    for name, fn in checks.items():
        if a.only and name not in a.only:
            continue
        fn()

    if a.json:
        json.dump({"draft": a.draft, "findings": findings}, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print(f"{a.draft}: {len(prose)} prose paragraphs, {len(sents)} sentences "
              f"(body up to the bibliography)\n")
        for name in checks:
            wanted = ("paragraphs", "sentences") if name == "shape" else (name,)
            items = [f for f in findings if f["check"] in wanted]
            if not items:
                continue
            print(f"## {name}  ({len(items)})")
            for f in items:
                print(f"  {f['headline']}")
                for e in f["excerpts"]:
                    print(f"      {e}")
            print()
        if not findings:
            print("nothing reported")

    hard = [f for f in findings if f["check"] not in ("paragraphs", "sentences", "punctuation")]
    sys.exit(1 if a.strict and hard else 0)


if __name__ == "__main__":
    main()

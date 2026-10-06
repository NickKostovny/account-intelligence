#!/usr/bin/env python3
"""Jev layer: rank the publication signals by how close each paper is to the bioprocess.

data/signals.csv holds 512 publication rows (Europe PMC, 49 accounts). They are unranked and 53
carry Claude's confidence=low. This asks Jev two questions per paper in ONE request:

  bioprocess_relevance  Score, 4 levels (not product / adjacent / CMC / process)
  process_data          Noul, is the paper about process data, modelling, PAT or automation?

State = {"title", "topic"} ONLY. Never named_people, pain_hypothesis (Claude-written), or anything
from people.csv / site_heads.csv; any contact-list name found in the text is replaced with
[person] before it leaves this machine. Everything after the answers is code: the gated display
values, the keep flag, the rank inside each account and the agreement numbers.

Agreement is against existing labels (Claude's high/medium/low confidence, title keywords), so it
is AGREEMENT, not accuracy. Nothing here writes to the SSOT; output goes to data/jev/ only.

  python3 jev_publications.py            # score all 512 -> data/jev/publication_relevance.csv
  python3 jev_publications.py probe 20   # first 20 rows only, print the answers (wording check)
"""
import csv, json, os, re, sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from jev_client import MODEL, ask, usage_summary
from pilot_jev import CHOICE_SURE, NOUL_SURE, person_names, rows

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "jev")
WORKERS = 6
TAG = "publications"
KEEP_SCORE, KEEP_CONF = 1.5, 0.6          # keep rule, in code: score >= 1.5 and confidence >= 0.6

LEVELS = ["not_product", "adjacent", "cmc", "process"]
QS = {
    "bioprocess_relevance": {
        "type": "score",
        "instructions": "How close is this paper to the work of making a therapeutic product?",
        "criteria": [
            "The paper is not about making or developing a therapeutic product: basic biology, "
            "disease biology, epidemiology, or chemistry with no product.",
            "The paper is adjacent to making a product: a clinical study, pharmacology, discovery "
            "biology, drug or target design, or an assay method with no link to a manufacturing process.",
            "The paper is about the CMC side of a product: its manufacturing, formulation, drug "
            "product, stability, or the analytical characterization of the product.",
            "The paper is directly about the manufacturing process: process development, cell culture, "
            "purification, scale-up, tech transfer, MSAT, bioprocess data, or control of a manufacturing process.",
        ]},
    "process_data": {
        "type": "noul",
        "instructions": "Is this paper about using process or manufacturing data, modelling, PAT or automation?",
        "criteria": {
            "true": "The paper builds or uses process models, process analytical technology (PAT) such as "
                    "Raman or NIR, soft sensors, statistical or machine-learning analysis of process or "
                    "manufacturing data, digital twins, or automation of a process or a lab.",
            "false": "The paper does none of these. It is about the product, the biology, the chemistry "
                     "or a clinical result."}},
}

# existing keyword label on the TITLE (public text), for agreement only
PROCESS_RX = re.compile(r"\b(process|bioprocess|upstream|downstream|cell culture|fed-batch|perfusion|"
                        r"bioreactor|chromatograph\w*|purification|harvest|clarification|scale[- ]?up|"
                        r"tech(nology)? transfer|manufactur\w*|titer|viral clearance|filtration|CHO)\b", re.I)
DATA_RX = re.compile(r"\b(PAT|Raman|NIR|spectroscop\w*|model\w*|machine learning|deep learning|AI|"
                     r"digital|automat\w*|soft sensor\w*|multivariate|data)\b", re.I)


def pubs():
    return [s for s in rows("signals.csv") if s["signal_type"] == "publication"]


def scrub(text, names):
    """Replace any contact-list name with [person], whole words only ("Raman M" is a contact, but
    "Raman Monitoring" in a title is not that person). Returns (text, changed)."""
    out = text
    for n in names:
        if n in out:
            out = re.sub(r"\b%s\b" % re.escape(n), "[person]", out)
    return out, out != text


def pub_state(s, names):
    title, t1 = scrub(s["title"], names)
    topic, t2 = scrub(s["topic"], names)
    state = {k: v for k, v in (("title", title), ("topic", topic)) if v.strip()}
    return state, t1 or t2


def noul_display(p):
    return "yes" if p >= 1 - NOUL_SURE else "no" if p <= NOUL_SURE else "unsure"


def band_display(ans):
    if ans["confidence"] < CHOICE_SURE:
        return "unsure"
    top = max(ans["probabilities"], key=lambda k: ans["probabilities"][k])
    return LEVELS[int(top)]


def probs_text(ans):
    return "|".join(f"{k}:{ans['probabilities'][k]:.2f}" for k in sorted(ans["probabilities"], key=int))


def score_one(s, names):
    state, scrubbed = pub_state(s, names)
    return s, scrubbed, ask(state, QS, tag=TAG)["answers"]


def record(s, scrubbed, a):
    rel, pdn = a["bioprocess_relevance"], a["process_data"]
    keep = rel["score"] >= KEEP_SCORE and rel["confidence"] >= KEEP_CONF
    return {"signal_id": s["signal_id"], "account_id": s["account_id"], "site_id": s["site_id"],
            "date": s["date"],
            "relevance_score": round(rel["score"], 3), "relevance_conf": round(rel["confidence"], 3),
            "relevance_probs": probs_text(rel), "relevance_band": band_display(rel),
            "process_data_noul": round(pdn["noul"], 3), "process_data": noul_display(pdn["noul"]),
            "keep": keep, "rank_in_account": 0,
            "claude_confidence": s["confidence"], "scrubbed_name": scrubbed, "model": MODEL}


def rank(recs):
    """Rank inside each account: kept first, then score, then process-data noul, then newest year."""
    by = defaultdict(list)
    for r in recs:
        by[r["account_id"]].append(r)
    for rs in by.values():
        rs.sort(key=lambda r: (-r["keep"], -r["relevance_score"], -r["process_data_noul"], -int(r["date"] or 0)))
        for i, r in enumerate(rs, 1):
            r["rank_in_account"] = i


def write(name, recs):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)
    return path


def by_account(recs):
    out = []
    for aid in sorted({r["account_id"] for r in recs}):
        rs = [r for r in recs if r["account_id"] == aid]
        band = Counter(r["relevance_band"] for r in rs)
        low = [r for r in rs if r["claude_confidence"] == "low"]
        out.append({"account_id": aid, "n": len(rs), "kept": sum(r["keep"] for r in rs),
                    **{"band_" + b: band.get(b, 0) for b in LEVELS + ["unsure"]},
                    "process_data_yes": sum(r["process_data"] == "yes" for r in rs),
                    "claude_low": len(low), "claude_low_below_bar": sum(not r["keep"] for r in low)})
    return out


def pct(a, b):
    return f"{a}/{b} ({a / max(1, b):.0%})"


def report(recs, src):
    n = len(recs)
    print(f"publications scored: {n} across {len({r['account_id'] for r in recs})} accounts")
    print(f"  names scrubbed before sending: {sum(r['scrubbed_name'] for r in recs)}")
    print("  gated band:", dict(Counter(r["relevance_band"] for r in recs)))
    print("  process_data:", dict(Counter(r["process_data"] for r in recs)))
    print(f"  keep (score>={KEEP_SCORE}, conf>={KEEP_CONF}): {pct(sum(r['keep'] for r in recs), n)}")
    print("agreement with existing labels (agreement, not accuracy):")
    for c in ("high", "medium", "low"):
        rs = [r for r in recs if r["claude_confidence"] == c]
        print(f"  Claude confidence={c:<6} n={len(rs):>3}  Jev keep {pct(sum(r['keep'] for r in rs), len(rs))}")
    low = [r for r in recs if r["claude_confidence"] == "low"]
    print(f"  the {len(low)} Claude-low rows: below the keep bar {sum(not r['keep'] for r in low)}, "
          f"kept {sum(r['keep'] for r in low)}")
    hi_lo = [r for r in recs if r["claude_confidence"] in ("high", "low")]
    ag = sum(r["keep"] == (r["claude_confidence"] == "high") for r in hi_lo)
    print(f"  keep vs Claude high/low (medium left out): {pct(ag, len(hi_lo))}")
    title = {s["signal_id"]: s["title"] for s in src}
    dec = [r for r in recs if r["relevance_band"] != "unsure"]
    ag = sum((r["relevance_band"] == "process") == bool(PROCESS_RX.search(title[r["signal_id"]])) for r in dec)
    print(f"  band=process vs process keyword in title (decided rows): {pct(ag, len(dec))}")
    dec = [r for r in recs if r["process_data"] != "unsure"]
    ag = sum((r["process_data"] == "yes") == bool(DATA_RX.search(title[r["signal_id"]])) for r in dec)
    print(f"  process_data vs data/PAT/model keyword in title (decided rows): {pct(ag, len(dec))}")
    print("  by year:", dict(Counter(r["date"] for r in recs if r["keep"])), "(kept rows)")


def main(limit=None):
    print("Jev usage before:", usage_summary())
    src, names = pubs(), person_names()
    todo = src[:limit] if limit else src
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(lambda s: score_one(s, names), todo))
    recs = [record(s, sc, a) for s, sc, a in results]
    rank(recs)
    if limit:
        for r in recs:
            t = next(s["title"] for s in src if s["signal_id"] == r["signal_id"])
            print(f"{r['relevance_score']:.2f} c{r['relevance_conf']:.2f} {r['relevance_band']:<11} "
                  f"pd{r['process_data_noul']:.2f} {r['claude_confidence']:<6} {t[:80]}")
    else:
        print("->", write("publication_relevance.csv", recs))
        print("->", write("publication_relevance_by_account.csv", by_account(recs)))
    report(recs, src)
    print("Jev usage after:", usage_summary())
    print("this layer:", layer_cost())


def layer_cost():
    p = os.path.join(HERE, "data", "cache", "jev", "usage.jsonl")
    rs = [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.exists(p) else []
    rs = [r for r in rs if r.get("tag") == TAG]
    tin = sum((r.get("usage") or {}).get("input_tokens", 0) for r in rs)
    return f"{len(rs)} uncached calls, {tin:,} input tokens, ${tin * 0.042 / 1e6:.4f}"


if __name__ == "__main__":
    arg = sys.argv[1:] or ["run"]
    if arg[0] == "probe":
        main(int(arg[1]) if len(arg) > 1 else 20)
    elif arg[0] == "run":
        main()
    else:
        print(__doc__)
        sys.exit(1)

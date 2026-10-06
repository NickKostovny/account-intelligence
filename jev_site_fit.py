#!/usr/bin/env python3
"""Site ICP fit (yes / soft / no) from the Jev site pilot, with a reason tied to PD/MSAT/CMC.

Wishlist: "ICP fit: yes/soft/no with a clear explanation tied to whether PD/MSAT/CMC are based there".

Reuses pilot_jev.SITE_QS, site_state() and site_verdict() byte for byte, so every request is the
one the 2026-09-28 pilot already sent and the answers come from the jev_client cache (~$0). What
leaves this machine is the site text only (company, site, city, region, functions, modality,
acquired_lineage) with verdict words stripped and roster names scrubbed by pilot_jev.site_state().

Verdict -> icp_fit (normalize.icp_from_verdict): KEEP=yes, FLAG=soft, GAP=soft, DROP=no.
CLOSED -> no if Claude's map says DROP, else soft. REVIEW -> Claude's icp_fit kept, display 'review'.
Nick's 2026-09-28 policy is the one pilot_jev encodes: small-molecule-only and mixed -> FLAG,
R&D-only -> FLAG, CDMO = customer (KEEP, info tag), animal health KEEP (Nick, 2026-09-30).

Every score is AGREEMENT with Claude's site map or a keyword check, never accuracy. The SSOT
(data/*.csv) is read, never written. Outputs go to data/jev/ only:

  python3 jev_site_fit.py            # site_fit.csv + site_label_sample.csv + report
  python3 jev_site_fit.py report     # report only, from the cached answers
"""
import csv, os, random, re, sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pilot_jev
from jev_client import MODEL, ask, usage_summary
from normalize import icp_from_verdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "jev")
SAMPLE_N, SEED = 60, 20260928
NOUL_YES, NOUL_NO, CHOICE_SURE = 0.8, 0.2, 0.8
NOULS = ("drug_substance", "pd_msat", "drug_product", "manufacturing", "support", "research", "cdmo")
CHOICES = ("status", "modality", "business")
SHORT = {"drug_substance": "DS", "pd_msat": "PD", "drug_product": "DP", "manufacturing": "MFG"}
MOD_WORD = {"biologic_only": "biologics", "small_molecule_only": "small molecule", "mixed": "mixed modality"}
TAG_TEXT = {"small-mol": "small-molecule only", "mixed": "biologic + small-molecule mix", "R&D-only": "research only, no manufacturing",
            "unclear-mfg": "no manufacturing or PD named", "support-only": "support functions only", "non-core": "not human pharma",
            "greenfield": "planned, not running yet", "closed": "closed, closing or sold"}

# keyword checks on Claude's own text (the 'keyword presence' labels)
KW_PD = re.compile(r"\b(process development|PD|MSAT|process sciences?|CMC|scale-?up|tech(nology)? transfer)\b", re.I)
KW_SM = re.compile(r"small[- ]molecule|\bAPIs?\b|oral solid|tablet|capsule|generic|synthetic|chemical", re.I)
KW_BIO = re.compile(r"biologic|\bmAbs?\b|antibod|protein|vaccine|cell therap|gene therap|\bAAV\b|mRNA|\bLNP\b|"
                    r"plasma|\bADCs?\b|biosimilar|viral vector|insulin|enzyme", re.I)


def rows(name):
    return list(csv.DictReader(open(os.path.join(HERE, "data", name), encoding="utf-8")))


def write(name, recs):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)
    return path


# ---------------------------------------------------------------- gated display values
def noul_show(p):
    return "yes" if p >= NOUL_YES else "no" if p <= NOUL_NO else "unsure"


def choice_show(ans):
    return ans["choice"] if ans["confidence"] >= CHOICE_SURE else "unsure"


# ---------------------------------------------------------------- Claude's labels
def claude_labels(s, tag):
    """(claude_verdict, policy_verdict, policy_reason). Same policy rewrite pilot_jev.run_site() scores against."""
    gold = "DROP" if s["verdict_manual"].lower() == "drop" else s["verdict_auto"]
    if s["region"] == "EU" and gold == "KEEP" and tag in ("small-mol", "mixed"):
        return gold, "FLAG", "EU small-mol/mixed re-pass"
    if gold == "DROP" and tag == "R&D-only":
        return gold, "FLAG", "R&D-only is FLAG"
    return gold, gold, ""


def icp_fit(verdict, claude_verdict, claude_icp):
    if verdict == "REVIEW":
        return claude_icp, "review"
    if verdict == "CLOSED":
        fit = "no" if claude_verdict == "DROP" else "soft"
    else:
        fit = icp_from_verdict(verdict)
    return fit, fit


# ---------------------------------------------------------------- reason + explanation, built in code
def keep_tag(a):
    parts = [SHORT[q] for q in ("pd_msat", "drug_substance", "drug_product") if a[q]["noul"] >= NOUL_YES]
    if not parts and a["manufacturing"]["noul"] >= NOUL_YES:
        parts = ["MFG"]
    return "+".join(parts) + " " + MOD_WORD[a["modality"]["choice"]]


def explain(verdict, tag, unsure, a):
    pd = noul_show(a["pd_msat"]["noul"])
    pd_txt = {"yes": "PD/MSAT/CMC is based here", "no": "no PD/MSAT/CMC named here",
              "unsure": "PD/MSAT/CMC here is unclear"}[pd]
    made = [n for q, n in (("drug_substance", "drug substance"), ("drug_product", "drug product"))
            if a[q]["noul"] >= NOUL_YES]
    made_txt = ("makes " + " and ".join(made)) if made else \
        ("manufacturing named" if a["manufacturing"]["noul"] >= NOUL_YES else "no manufacturing named")
    mod = choice_show(a["modality"])
    mod_txt = "modality unclear" if mod == "unsure" else MOD_WORD.get(mod, "modality not stated")
    head = f"{pd_txt}; {made_txt}; {mod_txt}"
    if verdict == "KEEP":
        return head + ("; contract manufacturer (a customer)" if tag.endswith("CDMO") else "")
    if verdict == "REVIEW":
        return head + f"; needs a person: Jev unsure on {unsure}"
    return head + f"; {verdict.lower()}: {TAG_TEXT.get(tag, tag)}"


# ---------------------------------------------------------------- keyword agreement labels
def kw_modality(text):
    sm, bio = bool(KW_SM.search(text)), bool(KW_BIO.search(text))
    return "mixed" if sm and bio else "small_molecule_only" if sm else "biologic_only" if bio else "not_stated"


# ---------------------------------------------------------------- per site
def fit_row(s, a, leaked):
    verdict, tag, unsure = pilot_jev.site_verdict(a)
    claude_v, policy_v, policy_why = claude_labels(s, tag)
    info = []
    if verdict == "KEEP":
        info = ["CDMO"] if tag == "CDMO" else []
        tag = keep_tag(a) + (" · CDMO" if tag == "CDMO" else "")
    animal = a["business"]["choice"] == "animal_health" and a["business"]["confidence"] >= 0.8
    if animal:
        info.append("animal health")
        # Nick 2026-09-30 keeps animal health: a map FLAG on an animal-health site that the rule now
        # keeps was the animal-health FLAG, so the policy map moves with it
        if verdict == "KEEP" and policy_v == "FLAG":
            policy_v, policy_why = "KEEP", "animal health is KEEP"
    fit, fit_show = icp_fit(verdict, claude_v, s["icp_fit"])
    decided = verdict != "REVIEW"
    rec = {"site_id": s["site_id"], "account_id": s["account_id"], "region": s["region"],
           "jev_verdict": verdict, "reason_tag": tag, "info_tag": "; ".join(info), "unsure_on": unsure,
           "icp_fit": fit, "icp_fit_display": fit_show,
           "explanation": explain(verdict, tag, unsure, a),
           "claude_verdict": claude_v, "claude_icp_fit": s["icp_fit"], "policy_verdict": policy_v,
           "changed_by_policy": policy_v != claude_v, "policy_reason": policy_why,
           "agree_verdict": decided and (verdict == policy_v or (verdict == "CLOSED" and policy_v in ("GAP", "DROP"))),
           "agree_icp_fit": decided and fit == icp_from_verdict(policy_v) if policy_v != claude_v
           else decided and fit == s["icp_fit"],
           "short_text": len(s["functions"]) <= 40, "leaked_marker": leaked,
           "kw_pd": bool(KW_PD.search(s["functions"] + " " + s["site"])),
           "kw_modality": kw_modality(s["modality"] + " " + s["functions"])}
    for q in NOULS:
        rec[q + "_p"] = round(a[q]["noul"], 3)
        rec[q] = noul_show(a[q]["noul"])
    for q in CHOICES:
        rec[q + "_top"] = a[q]["choice"]
        rec[q + "_conf"] = round(a[q]["confidence"], 3)
        rec[q] = choice_show(a[q])
    pr = a["status"]["probabilities"]
    rec["status_p_closed"] = round(sum(pr.get(o, 0) for o in ("closed", "sold_or_divested", "closure_announced")), 3)
    rec["status_p_planned"] = round(pr.get("planned_not_running", 0), 3)
    rec["model"] = MODEL
    return rec


def run_all():
    sites, names = rows("sites.csv"), pilot_jev.person_names()

    def one(s):
        state, leaked = pilot_jev.site_state(s, names)
        return s, state, leaked, ask(state, pilot_jev.SITE_QS, tag="site")

    with ThreadPoolExecutor(pilot_jev.WORKERS) as ex:
        results = list(ex.map(one, sites))
    models = Counter(r[3].get("model") for r in results)
    recs = [fit_row(s, resp["answers"], leaked) for s, _, leaked, resp in results]
    return sites, names, results, recs, models


# ---------------------------------------------------------------- 60-row label sheet
def scrub(text, names):
    t = pilot_jev.LEAK.sub("", text or "")
    for n in names:
        t = t.replace(n, "[person]")
    return re.sub(r"\s{2,}", " ", t).strip(" -—")


def sample(recs, n=SAMPLE_N, seed=SEED):
    """Disagreements ~40%, REVIEW ~25%, agreements the rest; round-robin over region x Claude x Jev strata."""
    rnd = random.Random(seed)
    buckets = {"disagree": [r for r in recs if r["jev_verdict"] != "REVIEW" and not r["agree_verdict"]],
               "review": [r for r in recs if r["jev_verdict"] == "REVIEW"],
               "agree": [r for r in recs if r["agree_verdict"]]}
    quota = {"disagree": round(n * 0.4), "review": round(n * 0.25)}
    quota["agree"] = n - quota["disagree"] - quota["review"]
    picked = []
    for b, rs in buckets.items():
        strata = {}
        for r in rs:
            strata.setdefault((r["region"], r["policy_verdict"], r["jev_verdict"]), []).append(r)
        order = sorted(strata)
        for k in order:
            rnd.shuffle(strata[k])
        take = []
        while len(take) < min(quota[b], len(rs)):
            for k in order:
                if strata[k] and len(take) < quota[b]:
                    take.append(strata[k].pop())
        picked += [(b, r) for r in take]
    rnd.shuffle(picked)
    return picked


def label_sheet(picked, sites, names):
    by_id = {s["site_id"]: s for s in sites}
    company = {a["account_id"]: a["company"] for a in rows("accounts.csv")}
    out = []
    for _, r in picked:
        s = by_id[r["site_id"]]
        out.append({"site_id": s["site_id"], "account": company.get(s["account_id"], s["account_id"]),
                    "site": scrub(s["site"], names), "city": s["city"],
                    "functions": scrub(s["functions"], names), "modality": scrub(s["modality"], names),
                    "nick_verdict": "", "nick_note": ""})
    return out


# ---------------------------------------------------------------- report
def pct(a, b):
    return f"{a}/{b} ({a / max(1, b):.0%})"


def report(recs, picked, models):
    n = len(recs)
    dec = [r for r in recs if r["jev_verdict"] != "REVIEW"]
    print(f"model(s) in responses: {dict(models)}")
    print(f"sites {n}  decided {pct(len(dec), n)}  REVIEW {n - len(dec)}")
    print("jev_verdict:", dict(Counter(r["jev_verdict"] for r in recs).most_common()))
    print("icp_fit_display:", dict(Counter(r["icp_fit_display"] for r in recs).most_common()))
    print("reason_tag (non-KEEP):", dict(Counter(r["reason_tag"] for r in recs if r["jev_verdict"] not in ("KEEP",)).most_common()))
    print("reason_tag (KEEP):", dict(Counter(r["reason_tag"] for r in recs if r["jev_verdict"] == "KEEP").most_common()))
    print("info_tag:", dict(Counter(r["info_tag"] for r in recs if r["info_tag"]).most_common()))
    print("unsure_on:", dict(Counter(r["unsure_on"] for r in recs if r["unsure_on"]).most_common()))
    print(f"map rows changed by Nick's policy: {sum(r['changed_by_policy'] for r in recs)} "
          f"{dict(Counter(r['policy_reason'] for r in recs if r['policy_reason']))}")
    print("AGREEMENT with Claude's map (under policy), decided sites only -- not accuracy:")
    print(f"  verdict {pct(sum(r['agree_verdict'] for r in dec), len(dec))}   "
          f"icp_fit {pct(sum(r['agree_icp_fit'] for r in dec), len(dec))}")
    for label, rs in (("US full text", [r for r in dec if r["region"] == "US" and not r["short_text"]]),
                      ("EU full text", [r for r in dec if r["region"] == "EU" and not r["short_text"]]),
                      ("short tag lists", [r for r in dec if r["short_text"]])):
        print(f"  {label:<16} verdict {pct(sum(r['agree_verdict'] for r in rs), len(rs))}")
    labels = ["KEEP", "FLAG", "GAP", "DROP", "CLOSED", "REVIEW"]
    print("  confusion rows=Claude(policy) cols=Jev:", " ".join(f"{l:>6}" for l in labels))
    for g in ["KEEP", "FLAG", "GAP", "DROP"]:
        print(f"  {g:>40}", " ".join(f"{sum(1 for r in recs if r['policy_verdict'] == g and r['jev_verdict'] == l):>6}"
                                    for l in labels))
    pd = [r for r in recs if r["pd_msat"] != "unsure"]
    print(f"AGREEMENT PD/MSAT/CMC: Jev gated pd_msat vs keyword in Claude's text: "
          f"{pct(sum((r['pd_msat'] == 'yes') == r['kw_pd'] for r in pd), len(pd))}  "
          f"(Jev yes & no keyword {sum(r['pd_msat'] == 'yes' and not r['kw_pd'] for r in pd)}, "
          f"keyword & Jev no {sum(r['pd_msat'] == 'no' and r['kw_pd'] for r in pd)}, unsure {n - len(pd)})")
    md = [r for r in recs if r["modality"] != "unsure"]
    print(f"AGREEMENT modality: Jev gated modality vs keyword label on Claude's text: "
          f"{pct(sum(r['modality'] == r['kw_modality'] for r in md), len(md))}  (unsure {n - len(md)})")
    print("label sheet buckets:", dict(Counter(b for b, _ in picked)),
          " strata:", len({(r['region'], r['policy_verdict'], r['jev_verdict']) for _, r in picked}),
          " regions:", dict(Counter(r["region"] for _, r in picked)))


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step not in ("all", "report"):
        print(__doc__)
        sys.exit(1)
    print("Jev usage before:", usage_summary())
    sites, names, results, recs, models = run_all()
    picked = sample(recs)
    if step == "all":
        print("->", write("site_fit.csv", recs))
        print("->", write("site_label_sample.csv", label_sheet(picked, sites, names)))
    report(recs, picked, models)
    print("Jev usage after: ", usage_summary())

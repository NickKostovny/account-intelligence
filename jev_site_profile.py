#!/usr/bin/env python3
"""Jev layer: what each site DOES (function chips) and MAKES (modality chips), rolled up per account.

One request per site. State = pilot_jev.site_state(row, names)[0] (site text only: verdict words
stripped, roster names scrubbed; Nick approved sending it 2026-09-28). Questions = one POSITIVE
Noul per function and per modality, all in the same request, because the state is billed once.
Code does the rest: gating, chips, the account rollup and the counts.

Gate: a Noul is shown only when decisive -- >= 0.8 is "yes", <= 0.2 is "no", else "unsure".

Validation is AGREEMENT with plain keyword presence in the same text, not accuracy: a keyword can
sit inside "no manufacturing on site", and Jev can read "CAR-T" as cell therapy without the word.
The disagreement buckets say which of those it was.

Writes only data/jev/ (site_profile.csv, account_profile.csv, site_profile_disagreements.csv).

  python3 jev_site_profile.py probe       # 5 sites, prints the answers (Jev calls, cached)
  python3 jev_site_profile.py run         # all sites -> site_profile.csv + account_profile.csv
  python3 jev_site_profile.py validate    # keyword agreement (no Jev calls; reads site_profile.csv)
  python3 jev_site_profile.py all         # run + validate
"""
import csv, json, os, re, sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import jev_client
from jev_client import ask, usage_summary
from pilot_jev import site_state, person_names, rows, LEAK

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "jev")
TAG = "site_profile"
WORKERS = 6
YES, NO = 0.8, 0.2
BUDGET = 1.00                                   # dollars, this layer only

# label -> (short name shown on the chip, what counts as a yes, keyword regex for validation)
FUNCTIONS = {
    "research": ("Research",
                 "research, discovery, drug discovery, early research, research labs or R&D",
                 r"research|discover|\bR&D\b|\bR\s*&\s*D\b|early[- ]stage lab"),
    "process_development": ("Process development",
                            "process development, PD, process sciences, upstream or downstream development, "
                            "cell line development, scale-up or clinical process development",
                            r"process[- ]dev|\bPD\b|process scien|cell[- ]line dev|scale[- ]up|"
                            r"(upstream|downstream|USP|DSP)[^;.]{0,20}develop"),
    "msat": ("MSAT",
             "MSAT, manufacturing science and technology, manufacturing sciences, technical services for "
             "manufacturing, or technology transfer into manufacturing",
             r"\bMSAT\b|manufacturing scien|tech(nology)?[- ]transfer|technical services|tech ops|technical operations"),
    "cmc": ("CMC",
            "CMC, chemistry manufacturing and controls, CMC development or CMC operations",
            r"\bCMC\b|chemistry,? manufacturing,? (and|&) controls"),
    # wishlist "GMP manufacturing". A long "GMP or cGMP or commercial or ..." list left 207/604 sites unsure
    # (Jev reads it literally: "biologics manufacturing" is not "GMP"); plain "manufacturing or production"
    # resolved 25 of 30 sampled unsure sites and flipped 0 of 20 decided ones.
    "manufacturing": ("Manufacturing",
                      "manufacturing or production",
                      r"\bc?GMP\b|manufactur|\bmfg\b|production|\bplant\b|biomanufactur",
                      "manufacturing, production or a manufacturing plant at this site, for example drug substance, "
                      "drug product, API, clinical or commercial manufacturing"),
    "drug_substance": ("Drug substance",
                       "drug substance, DS, API, active ingredient, bulk manufacturing, or upstream or downstream "
                       "manufacturing",
                       r"drug[- ]substance|\bDS\b|\bAPIs?\b|active (pharmaceutical )?ingredient|\bbulk\b|"
                       r"upstream|downstream|\bUSP\b|\bDSP\b"),
    "fill_finish": ("Fill-finish",
                    "fill-finish, aseptic filling, filling of vials, syringes, pens or cartridges, lyophilization, "
                    "or sterile drug product manufacturing",
                    r"fill[- /&]*finish|\bfill\b|filling|aseptic|lyophili|vials?\b|syringe|cartridge|sterile"),
    "qc": ("QC",
           "quality control, QC labs, release testing, stability testing or QC microbiology",
           r"\bQC\b|quality control|release test|stability test|QC micro"),
    "analytical_development": ("Analytical development",
                               "analytical development, analytical sciences, method development, assay development "
                               "or bioanalytical work",
                               r"analytic|method dev|assay dev|bioanalyt"),
}
MODALITIES = {
    "mab": ("mAbs",
            "monoclonal antibodies, mAbs or antibody products",
            r"\bmAbs?\b|monoclonal|antibod"),
    "adc": ("ADCs",
            "antibody-drug conjugates, ADCs, bioconjugates or payload-linker conjugation",
            r"\bADCs?\b|drug[- ]conjugate|bioconjugat|conjugation"),
    "other_protein": ("Bispecifics / other proteins",
                      "bispecific or multispecific antibodies, fusion proteins, enzymes, insulin, hormones, "
                      "cytokines, blood factors, or other recombinant therapeutic proteins",
                      r"bispecific|multispecific|trispecific|fusion protein|enzyme|insulin|hormone|cytokine|"
                      r"recombinant protein|therapeutic protein|protein therapeut|nanobod|factor VIII|factor IX"),
    "vaccine": ("Vaccines",
                "vaccines, vaccine antigens or immunization products",
                r"vaccin|antigen"),
    "cell_therapy": ("Cell therapy",
                     "cell therapy, CAR-T, TCR-T, NK cells, autologous or allogeneic cell products, or stem-cell "
                     "derived therapies",
                     r"cell[- ]therap|CAR[- ]?T\b|\bCGT\b|\bTCR|autologous|allogeneic|stem[- ]cell|iPSC|\bNK\b"),
    "gene_therapy": ("Gene therapy",
                     "gene therapy, AAV, viral vectors, lentiviral vectors, or gene editing",
                     r"gene[- ]therap|\bAAV\b|viral[- ]vector|lentivir|gene[- ]edit|CRISPR|\bCGT\b"),
    "mrna_lnp": ("mRNA / LNP",
                 "mRNA, messenger RNA, LNP or lipid nanoparticles",
                 r"\bmRNA\b|messenger RNA|\bLNPs?\b|lipid nanoparticle"),
    "rnai_oligo": ("RNAi / oligos",
                   "siRNA, RNAi, antisense oligonucleotides, ASOs or other oligonucleotides",
                   r"\bsiRNA\b|\bRNAi\b|oligonucleotide|\boligos?\b|antisense|\bASOs?\b"),
    "peptide": ("Peptides",
                "peptides, GLP-1 or incretin medicines, semaglutide or tirzepatide",
                r"peptide|GLP-1|incretin|semaglutide|tirzepatide"),
    "small_molecule": ("Small molecule",
                       "small molecules, chemical APIs, chemical synthesis, oral solid doses, tablets or capsules",
                       r"small[- ]molecule|small[- ]mol\b|chemical|synthe(tic|sis)|oral solid|tablet|capsule|\bOSD\b"),
    "plasma": ("Plasma",
               "plasma-derived products, plasma fractionation, immunoglobulins from plasma, or albumin",
               r"plasma|fractionat|albumin|immunoglobulin|\bIVIG\b|\bIg\b"),
}
NEGATION = re.compile(r"\b(no|not|non|without|never|none|former(ly)?|previously|divest\w*|sold|closed?|"
                      r"clos(ing|ure)|exit(ed)?|transferred|moved|wound|shut|outsourc\w*|relied on partners|"
                      r"rather than|instead of)\b[^;.]{0,60}$", re.I)


def fn_question(what, true_detail=None):
    return {"type": "noul", "instructions": f"Does the text name {what} as work done at this site?",
            "criteria": {"true": f"The text names {true_detail}." if true_detail else
                         f"The text names {what} as work done at this site.",
                         "false": "The text names other work at this site, such as research, development, offices "
                                  "or services, or gives no detail about the work." if true_detail else
                         "The text names other work at this site, or gives no detail about the work."}}


def mod_question(what):
    return {"type": "noul", "instructions": f"Does the text name {what} as products made or developed at this site?",
            "criteria": {"true": f"The text names {what} as products made or developed at this site.",
                         "false": "The text names other product types, or names no product type."}}


QUESTIONS = {**{f"fn_{k}": fn_question(v[1], *v[3:]) for k, v in FUNCTIONS.items()},
             **{f"mod_{k}": mod_question(v[1]) for k, v in MODALITIES.items()}}
LABELS = [("fn", k, v[0]) for k, v in FUNCTIONS.items()] + [("mod", k, v[0]) for k, v in MODALITIES.items()]


def gate(p):
    return "yes" if p >= YES else "no" if p <= NO else "unsure"


def write(name, recs):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()) if recs else ["empty"])
        w.writeheader()
        w.writerows(recs)
    return path


def layer_cost():
    p = os.path.join(jev_client.CACHE, "usage.jsonl")
    tin = sum((json.loads(l).get("usage") or {}).get("input_tokens", 0)
              for l in open(p, encoding="utf-8") if f'"tag": "{TAG}"' in l) if os.path.exists(p) else 0
    return tin * 0.042 / 1e6


def profile(site, names):
    state, leaked = site_state(site, names)
    resp = ask(state, QUESTIONS, tag=TAG)
    return state, leaked, resp


CGT_BOTH = re.compile(r"\bCGT\b|cell\s*(?:and|&|/|-and-)\s*gene\s*therap", re.I)


def site_record(s, state, leaked, resp):
    a = resp["answers"]
    rec = {"site_id": s["site_id"], "account_id": s["account_id"], "region": s["region"], "site": s["site"],
           "city": s["city"]}
    chips = {"fn": ([], []), "mod": ([], [])}
    raw = {}
    for kind, key, name in LABELS:
        p = a[f"{kind}_{key}"]["noul"]
        g = gate(p)
        raw[f"{kind}_{key}_p"], raw[f"{kind}_{key}"] = round(p, 3), g
        if g == "yes":
            chips[kind][0].append(name)
        elif g == "unsure":
            chips[kind][1].append(name)
    # Nick 2026-09-28 (Claude's recommendation): a bare "CGT" / "cell and gene therapy" means BOTH
    # chips; a site that names only one kind (CAR-T, AAV, viral vector) keeps Jev's split.
    cgt = bool(CGT_BOTH.search(" ".join((s.get("functions", ""), s.get("modality", "")))))
    if cgt:
        for key in ("cell_therapy", "gene_therapy"):
            name = next(n for k2, k, n in LABELS if k2 == "mod" and k == key)
            raw[f"mod_{key}"] = "yes"
            if name in chips["mod"][1]:
                chips["mod"][1].remove(name)
            if name not in chips["mod"][0]:
                chips["mod"][0].append(name)
    rec["cgt_rule"] = cgt
    rec.update({"function_chips": "|".join(chips["fn"][0]), "function_unsure": "|".join(chips["fn"][1]),
                "modality_chips": "|".join(chips["mod"][0]), "modality_unsure": "|".join(chips["mod"][1])})
    rec.update(raw)
    rec.update({"leaked_marker_stripped": leaked, "state_chars": len(json.dumps(state, ensure_ascii=False)),
                "model": resp.get("model", jev_client.MODEL)})
    return rec


def account_rollup(site_recs):
    accts = {r["account_id"]: r for r in rows("accounts.csv")}
    by = defaultdict(list)
    for r in site_recs:
        by[r["account_id"]].append(r)
    out = []
    for aid in sorted(by):
        rs, acc = by[aid], accts.get(aid, {})
        rec = {"account_id": aid, "company": acc.get("company", ""), "n_sites": len(rs)}
        foot, unsure, fn = [], [], []
        for kind, key, name in LABELS:
            n_yes = sum(r[f"{kind}_{key}"] == "yes" for r in rs)
            n_uns = sum(r[f"{kind}_{key}"] == "unsure" for r in rs)
            rec[f"{kind}_{key}_sites"] = n_yes
            if kind == "mod" and n_yes:
                foot.append((n_yes, name))
            if kind == "mod" and n_uns:
                unsure.append((n_uns, name))
            if kind == "fn" and n_yes:
                fn.append((n_yes, name))
        fmt = lambda xs: "|".join(f"{n}:{c}" for c, n in sorted(xs, key=lambda x: (-x[0], x[1])))
        rec = {**{k: rec[k] for k in ("account_id", "company", "n_sites")},
               "modality_footprint_jev": fmt(foot), "modality_unsure_sites": fmt(unsure),
               "function_counts": fmt(fn),
               "primary_modalities_ssot": acc.get("primary_modalities", ""),
               "modality_footprint_ssot": acc.get("modality_footprint", ""),
               **{k: v for k, v in rec.items() if k.endswith("_sites")},
               "model": jev_client.MODEL}
        out.append(rec)
    return out


def run(limit=None):
    before = usage_summary()
    sites, names = rows("sites.csv"), person_names()
    if limit:
        sites = sites[:limit]
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(lambda s: (s, *profile(s, names)), sites))
    recs = [site_record(s, st, lk, resp) for s, st, lk, resp in results]
    if limit:
        for r in recs:
            print(r["site_id"], "| fn:", r["function_chips"], "| ?", r["function_unsure"],
                  "| mod:", r["modality_chips"], "| ?", r["modality_unsure"],
                  "| p(small_mol)", r["mod_small_molecule_p"])
    else:
        p1 = write("site_profile.csv", recs)
        p2 = write("account_profile.csv", account_rollup(recs))
        print(f"-> {p1}  ({len(recs)} sites)\n-> {p2}")
    print("Jev usage before:", before)
    print("Jev usage after: ", usage_summary())
    cost = layer_cost()
    print(f"this layer ({TAG}): ${cost:.4f} of ${BUDGET:.2f} budget")
    if cost > BUDGET:
        raise SystemExit("over budget")


# ---------------------------------------------------------------- validation (no Jev calls)
def kw_hits(state, rx):
    """Keyword hits in the text fields, each tagged with its field and whether it sits after a negation."""
    hits = []
    for field in ("site", "functions", "modality", "acquired_lineage"):
        v = state.get(field, "")
        for m in re.finditer(rx, v, re.I):
            neg = bool(NEGATION.search(v[max(0, m.start() - 80):m.start()]))
            hits.append((field, m.group(), neg, v[max(0, m.start() - 60):m.end() + 60]))
    return hits


def validate():
    site_recs = rows(os.path.join("jev", "site_profile.csv"))
    sites = {s["site_id"]: s for s in rows("sites.csv")}
    names = person_names()
    specs = {**{("fn", k): v for k, v in FUNCTIONS.items()}, **{("mod", k): v for k, v in MODALITIES.items()}}
    dis, table = [], []
    print("Keyword agreement per label (Jev decisive answers vs keyword present in the same text; NOT accuracy)")
    print(f"  {'label':<30}{'jev yes':>8}{'unsure':>8}{'kw hit':>8}{'agree':>14}"
          f"{'J+ K-':>7}{'J- K+':>7}{'  of J- K+: negated / lineage-only / other'}")
    for kind, key, name in LABELS:
        rx = specs[(kind, key)][2]
        n = y = u = kw = ag = jp_kn = jn_kp = 0
        buckets = Counter()
        for r in site_recs:
            state, _ = site_state(sites[r["site_id"]], names)
            hits = kw_hits(state, rx)
            has = bool(hits)
            g = r[f"{kind}_{key}"]
            kw += has
            if g == "unsure":
                u += 1
                continue
            n += 1
            j = g == "yes"
            y += j
            ag += j == has
            if j and not has:
                jp_kn += 1
                txt = " ".join(state.get(f, "") for f in ("site", "functions", "modality"))
                dis.append({"site_id": r["site_id"], "label": f"{kind}_{key}", "direction": "jev_yes_kw_absent",
                            "p": r[f"{kind}_{key}_p"], "bucket": "synonym_or_inference", "field": "",
                            "keyword": "", "snippet": txt[:200]})
            elif has and not j:
                jn_kp += 1
                if all(h[2] for h in hits):
                    b = "negated_mention"
                elif all(h[0] == "acquired_lineage" for h in hits):
                    b = "lineage_only"
                else:
                    b = "other"
                buckets[b] += 1
                h = next((h for h in hits if not h[2]), hits[0])
                dis.append({"site_id": r["site_id"], "label": f"{kind}_{key}", "direction": "jev_no_kw_present",
                            "p": r[f"{kind}_{key}_p"], "bucket": b, "field": h[0], "keyword": h[1],
                            "snippet": h[3]})
        rate = f"{ag}/{n} {ag / max(1, n):.0%}"
        table.append({"label": f"{kind}_{key}", "name": name, "decided": n, "jev_yes": y, "unsure": u,
                      "kw_hit": kw, "agree": ag, "agree_pct": round(ag / max(1, n), 3),
                      "jev_yes_kw_absent": jp_kn, "jev_no_kw_present": jn_kp,
                      "neg": buckets["negated_mention"], "lineage": buckets["lineage_only"], "other": buckets["other"]})
        print(f"  {name:<30}{y:>8}{u:>8}{kw:>8}{rate:>14}{jp_kn:>7}{jn_kp:>7}"
              f"   {buckets['negated_mention']} / {buckets['lineage_only']} / {buckets['other']}")
    tot_ag = sum(t["agree"] for t in table)
    tot_n = sum(t["decided"] for t in table)
    tot_u = sum(t["unsure"] for t in table)
    print(f"  overall: agree {tot_ag}/{tot_n} ({tot_ag / max(1, tot_n):.0%}) of decided label-answers; "
          f"unsure {tot_u}/{tot_n + tot_u} ({tot_u / max(1, tot_n + tot_u):.0%})")
    p1 = write("site_profile_disagreements.csv", dis)
    p2 = write("site_profile_agreement.csv", table)
    print(f"-> {p1} ({len(dis)} rows)\n-> {p2}")
    validate_pilot(site_recs)
    validate_accounts()


def validate_pilot(site_recs):
    """Consistency with the earlier Jev site-verdict pass (differently worded questions, same text)."""
    path = os.path.join(HERE, "data", "pilot", "jev_site_verdict.csv")
    if not os.path.exists(path):
        return
    old = {r["site_id"]: r for r in csv.DictReader(open(path, encoding="utf-8"))}
    pairs = [("research", ["fn_research"]), ("drug_substance", ["fn_drug_substance"]),
             ("pd_msat", ["fn_process_development", "fn_msat"]), ("manufacturing", ["fn_manufacturing"]),
             ("drug_product", ["fn_fill_finish"])]
    print("Consistency with the site-verdict pilot (both Jev, different wording; both answers decisive):")
    for oq, new in pairs:
        n = ag = 0
        for r in site_recs:
            o = old.get(r["site_id"])
            if not o or not o.get(oq):
                continue
            og = gate(float(o[oq]))
            ns = [r[q] for q in new]
            if og == "unsure" or "unsure" in ns and "yes" not in ns:
                continue
            n += 1
            ag += (og == "yes") == ("yes" in ns)
        print(f"  {oq:<16} vs {'+'.join(new):<40} {ag}/{n} ({ag / max(1, n):.0%})")
    sm = [(o["modality"], r["mod_small_molecule"]) for r in site_recs
          for o in [old.get(r["site_id"])] if o and float(o.get("modality_conf") or 0) >= 0.8
          and r["mod_small_molecule"] != "unsure"]
    ok = sum((m in ("small_molecule_only", "mixed")) == (g == "yes") for m, g in sm)
    print(f"  modality Choice (small_molecule_only|mixed) vs Small molecule chip  {ok}/{len(sm)} ({ok / max(1, len(sm)):.0%})"
          "   (pilot 'mixed' can mean biologic+other, so some splits are expected)")


def validate_accounts():
    """Account footprint (code rollup of Jev chips) vs keyword presence in accounts.csv primary_modalities."""
    acc = rows(os.path.join("jev", "account_profile.csv"))
    print("Account footprint vs accounts.csv primary_modalities + modality_footprint keywords "
          "(agreement; SSOT text is company-level, sites are partial):")
    for key, (name, _, rx) in MODALITIES.items():
        n = ag = only_jev = only_ssot = 0
        for a in acc:
            ssot = (a["primary_modalities_ssot"] + " " + a["modality_footprint_ssot"]).strip()
            if not ssot:
                continue
            n += 1
            s = bool(re.search(rx, ssot, re.I))
            j = int(a[f"mod_{key}_sites"]) > 0
            ag += s == j
            only_jev += j and not s
            only_ssot += s and not j
        print(f"  {name:<30} {ag}/{n}  Jev-only {only_jev}  SSOT-only {only_ssot}")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    if step == "probe":
        run(limit=5)
    elif step == "run":
        run()
    elif step == "validate":
        validate()
    elif step == "all":
        run()
        validate()
    else:
        print(__doc__)
        sys.exit(1)

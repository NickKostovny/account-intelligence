#!/usr/bin/env python3
"""Jev layer on the job-postings index: job family, digital/IT, bioprocess core and seniority
for every UNIQUE role title the aiq.classify() regex left unlabeled, then hiring rollups in code.

aiq.classify() labels job_family on ~15% of postings.csv. For each unique normalized role_title
among rows with an EMPTY job_family and no exclude, Jev gets ONE request (state = the title and
the account id, both public text) with four questions: job_family (Choice over aiq.FAMILIES +
other), digital_data_it (Noul), bioprocess_core (Noul), seniority_band (Choice). A Choice is
shown only at confidence >= 0.8 and a Noul only at >= 0.8 (yes) / <= 0.2 (no); anything else is
'unsure'. Counts, dates and the 365-day window are code, never Jev.

Scores against the regex are AGREEMENT, not accuracy, and the validation sample is biased to
titles the regex could read (templated, keyword-bearing).

Nothing here writes to the SSOT tables. Output goes to data/jev/ only.

  python3 jev_postings.py sample    # 400 regex-labeled titles -> agreement per family + cost projection
  python3 jev_postings.py label     # every unlabeled, not-excluded title (refuses if projection >= $1.00)
  python3 jev_postings.py rollup    # code only: site_hiring.csv + account_hiring.csv
  python3 jev_postings.py all
"""
import csv, datetime, hashlib, json, os, random, re, sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import aiq
from jev_client import CACHE, MODEL, ask, usage_summary

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "jev")
WORKERS = 6
SAMPLE_N, SEED = 400, 20260928
BUDGET = 1.00                                   # dollars, whole layer
PRICE = 0.042 / 1e6                             # per input token
TAGS = ("postings_sample", "postings_label")
NOUL_SURE, CHOICE_SURE = 0.2, 0.8
FAMILY_NAMES = [f for f, _ in aiq.FAMILIES]
BIOPROCESS_REGEX = {"msat_mfg_sci", "process_dev"}   # what bioprocess_core means for a regex-labeled row
WINDOW_DAYS = 365


def rows(name):
    return list(csv.DictReader(open(os.path.join(HERE, "data", name), encoding="utf-8")))


def write(name, recs, fields=None):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    fields = fields or (list(recs[0].keys()) if recs else [])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(recs)
    return path


# ---------------------------------------------------------------- questions
# Criteria restate the aiq.FAMILIES regex in words, so a Jev family means what a regex family means.
FAMILY_Q = {
    "type": "choice",
    "instructions": "Which job family does the job title `role_title` belong to? Judge the person's own "
                    "discipline from the title. If the title names two families, pick the person's own "
                    "discipline: a data scientist for cell therapy is data_digital, an automation engineer "
                    "for upstream is automation_mes.",
    "criteria": {
        "lab_systems": "Laboratory informatics systems: LIMS, ELN, Benchling, LabWare, LabVantage, Empower, "
                       "Chromeleon, Unicorn, Genedata, Discoverant, lab informatics or sample management.",
        "automation_mes": "Process automation and control systems: automation, MES (manufacturing execution "
                          "system), SCADA, PLC, DCS, data historian or OSI PI, PAT (process analytical "
                          "technology), process control, instrumentation, GAMP or control systems.",
        "data_digital": "Data, digital and computation: data science, data engineering, data analytics, data "
                        "architecture, data governance or stewardship, data platforms or data management, "
                        "digital, analytics, informatics or bioinformatics, statistics or biostatistics, machine "
                        "learning or AI, modelling or simulation, computational science, software, or IT for "
                        "operations.",
        "msat_mfg_sci": "Manufacturing science and technology: MSAT, manufacturing sciences, technical "
                        "operations, technology transfer, manufacturing technology or process engineering.",
        "cell_gene": "Cell and gene therapy: the title names cell therapy, gene therapy, viral vectors, CAR-T, "
                     "lentivirus, AAV or apheresis.",
        "process_dev": "Process development and CMC: process development, upstream or downstream processing, "
                       "purification, cell culture, fermentation, chromatography, drug substance, drug product, "
                       "formulation, fill-finish, bioprocess, CMC or cell line development.",
        "analytical": "Analytical science and quality control labs: analytical development or chemistry, "
                      "bioassay, potency, characterization, QC or quality control, or method development.",
        "other": "Any other role, for example sales, marketing, medical affairs, clinical, regulatory, quality "
                 "assurance, HR, finance, legal, facilities, general manufacturing operations, research not "
                 "named above, or general management.",
    },
}
DIGITAL_Q = {
    "type": "noul",
    "instructions": "Is `role_title` a digital, data, analytics, informatics, IT or automation role?",
    "criteria": {
        "true": "The title names work in digital, data, analytics, informatics or bioinformatics, IT or "
                "information systems, software, cybersecurity, cloud or enterprise systems such as SAP, or "
                "process automation and control systems.",
        "false": "The title names none of these. A scientist, sales representative or manufacturing operator "
                 "who only uses data or systems in other work is no.",
    },
}
BIOPROCESS_Q = {
    "type": "noul",
    "instructions": "Is `role_title` a process development, MSAT, manufacturing science, CMC or bioprocess "
                    "engineering role?",
    "criteria": {
        "true": "The title names process development, MSAT or manufacturing science and technology, CMC, "
                "bioprocess or process engineering, upstream or downstream processing, purification, cell "
                "culture, technology transfer or process scale-up.",
        "false": "The title names none of these, for example sales, quality assurance, general manufacturing "
                 "operations, research, IT or corporate roles.",
    },
}
SENIORITY_Q = {
    "type": "choice",
    "instructions": "What seniority does the job title `role_title` state?",
    "criteria": {
        "executive_vp": "Vice president (VP, SVP, EVP, AVP), chief officer, president, or general manager of "
                        "a business or country.",
        "director_head": "Director, senior director, associate director, executive director, or head of a "
                         "department, function or site.",
        "manager": "Manager, senior manager, or team or group leader.",
        "individual_contributor": "A professional who does not manage others: scientist, engineer, specialist, "
                                  "analyst, consultant, principal or senior professional.",
        "entry_or_technician": "An entry-level, technician, operator, assistant, trainee or graduate role.",
        "unclear": "The title does not show the seniority.",
    },
}
QUESTIONS = {"job_family": FAMILY_Q, "digital_data_it": DIGITAL_Q,
             "bioprocess_core": BIOPROCESS_Q, "seniority_band": SENIORITY_Q}


# ---------------------------------------------------------------- postings
def title_key(t):
    return re.sub(r"[^a-z0-9]+", " ", aiq.afold(t or "").lower()).strip()


def postings():
    """postings.csv deduped by url. 6,020 urls are listed twice (AstraZeneca + an Alexion copy of the
    same careers.astrazeneca.com req); keep the row whose account owns the careers host."""
    by = defaultdict(list)
    for r in rows("postings.csv"):
        by[r["url"]].append(r)

    def owner(r):
        d = aiq.DOMAIN.get(r["account_id"], "")
        return bool(d) and d.split(".")[0] in (r["careers_host"] or "")

    return [next((r for r in grp if owner(r)), grp[0]) for grp in by.values()]


def title_sets(ps):
    """(unlabeled {key: [rows]}, labeled {key: [rows]}); both drop excluded rows and empty titles."""
    unl, lab = defaultdict(list), defaultdict(list)
    for r in ps:
        k = title_key(r["role_title"])
        if not k or r["excluded"]:
            continue
        (lab if r["job_family"] else unl)[k].append(r)
    return unl, lab


def sample_keys(lab):
    return random.Random(SEED).sample(sorted(lab), min(SAMPLE_N, len(lab)))


def state_for(grp):
    """Most common spelling of the title, most common account (alphabetical on ties)."""
    title = Counter(r["role_title"].strip() for r in grp).most_common(1)[0][0]
    acc = sorted(Counter(r["account_id"] for r in grp).items(), key=lambda x: (-x[1], x[0]))[0][0]
    return {"role_title": title, "company": acc}


# ---------------------------------------------------------------- Jev
def ask_all(keys, groups, tag):
    def one(k):
        st = state_for(groups[k])
        try:
            resp = ask(st, QUESTIONS, tag=tag)
            return k, st, resp["answers"], (resp.get("usage") or {}).get("input_tokens", 0), ""
        except Exception as e:                 # a failed title is reported, not fatal; rerun resumes from cache
            return k, st, None, 0, str(e)[:200]

    with ThreadPoolExecutor(WORKERS) as ex:
        out = list(ex.map(one, keys))
    errs = [o for o in out if o[4]]
    if errs:
        print(f"  {len(errs)} requests failed, first: {errs[0][4]}")
    return out


def gate_noul(p):
    return "yes" if p >= 1 - NOUL_SURE else ("no" if p <= NOUL_SURE else "unsure")


def gate_choice(a):
    return a["choice"] if a["confidence"] >= CHOICE_SURE else "unsure"


def label_row(k, st, a, grp, role):
    fam, dig, bio, sen = (a[q] for q in ("job_family", "digital_data_it", "bioprocess_core", "seniority_band"))
    regex = Counter(r["job_family"] for r in grp if r["job_family"]).most_common(1)
    return {
        "title_key": k, "role_title": st["role_title"], "company_sent": st["company"], "set": role,
        "n_postings": len(grp), "n_accounts": len({r["account_id"] for r in grp}),
        "regex_family": regex[0][0] if regex else "",
        "job_family_jev": fam["choice"], "job_family_conf": round(fam["confidence"], 3),
        "job_family_p": round(fam["probabilities"][fam["choice"]], 3), "job_family_gated": gate_choice(fam),
        "job_family_probs": json.dumps({o: round(p, 3) for o, p in sorted(fam["probabilities"].items(),
                                        key=lambda x: -x[1]) if p >= 0.01}),
        "digital_data_it_p": round(dig["noul"], 3), "digital_data_it_gated": gate_noul(dig["noul"]),
        "bioprocess_core_p": round(bio["noul"], 3), "bioprocess_core_gated": gate_noul(bio["noul"]),
        "seniority_jev": sen["choice"], "seniority_conf": round(sen["confidence"], 3),
        "seniority_gated": gate_choice(sen), "model": MODEL,
    }


def layer_spent():
    p = os.path.join(CACHE, "usage.jsonl")
    if not os.path.exists(p):
        return 0, 0.0
    rs = [json.loads(l) for l in open(p, encoding="utf-8")]
    rs = [r for r in rs if r.get("tag") in TAGS]
    tin = sum((r.get("usage") or {}).get("input_tokens", 0) for r in rs)
    return len(rs), tin * PRICE


# ---------------------------------------------------------------- sample: agreement with the regex
SEN_GOLD = {"vp": "executive_vp", "vice-president": "executive_vp", "chief": "executive_vp",
            "head-of": "director_head", "executive-director": "director_head", "senior-director": "director_head",
            "sr-director": "director_head", "director": "director_head", "associate-director": "director_head",
            "assoc-director": "director_head", "senior-manager": "manager", "manager": "manager",
            "principal": "individual_contributor", "senior": "individual_contributor",
            "sr": "individual_contributor", "staff": "individual_contributor"}   # 'lead' and default: no gold


def pct(a, b):
    return f"{a}/{b} ({a / b:.0%})" if b else "0/0"


def run_sample():
    ps = postings()
    _, lab = title_sets(ps)
    keys = sample_keys(lab)
    res = ask_all(keys, lab, "postings_sample")
    ok = [(k, st, a, t) for k, st, a, t, e in res if a]
    recs = [label_row(k, st, a, lab[k], "validation_sample") for k, st, a, _ in ok]
    print(f"validation sample: {len(recs)} regex-labeled titles (seed {SEED}); AGREEMENT with aiq.classify(), "
          f"biased to templated keyword titles")
    print(f"  {'regex family':<16} {'n':>4}  {'top choice agrees':>18}  {'gated decided':>14}  {'agree when decided':>19}")
    for fam in FAMILY_NAMES + ["ALL"]:
        rs = [r for r in recs if fam == "ALL" or r["regex_family"] == fam]
        if not rs:
            continue
        top = sum(r["job_family_jev"] == r["regex_family"] for r in rs)
        dec = [r for r in rs if r["job_family_gated"] != "unsure"]
        ag = sum(r["job_family_gated"] == r["regex_family"] for r in dec)
        print(f"  {fam:<16} {len(rs):>4}  {pct(top, len(rs)):>18}  {pct(len(dec), len(rs)):>14}  {pct(ag, len(dec)):>19}")
    miss = Counter((r["regex_family"], r["job_family_gated"]) for r in recs
                   if r["job_family_gated"] not in ("unsure", r["regex_family"]))
    print("  decided disagreements (regex -> jev):", dict(miss.most_common(8)))

    def noul_table(col, yes_fams):
        dec = [r for r in recs if r[col + "_gated"] != "unsure"]
        ag = sum((r[col + "_gated"] == "yes") == (r["regex_family"] in yes_fams) for r in dec)
        by = {f: dict(Counter(r[col + "_gated"] for r in recs if r["regex_family"] == f)) for f in FAMILY_NAMES}
        print(f"  {col}: decided {pct(len(dec), len(recs))}; agree with 'regex family in {sorted(yes_fams)}' "
              f"when decided {pct(ag, len(dec))}")
        print(f"    by regex family: {by}")

    noul_table("digital_data_it", aiq.ORG_CORE)
    noul_table("bioprocess_core", BIOPROCESS_REGEX)
    gold = {k: SEN_GOLD.get(Counter(r["seniority_rule"] for r in lab[k]).most_common(1)[0][0]) for k in keys}
    srs = [r for r in recs if gold.get(r["title_key"])]
    sdec = [r for r in srs if r["seniority_gated"] != "unsure"]
    sag = sum(r["seniority_gated"] == gold[r["title_key"]] for r in sdec)
    print(f"  seniority_band vs regex prefix rule: {len(srs)} titles with a rule; decided {pct(len(sdec), len(srs))}; "
          f"agree when decided {pct(sag, len(sdec))}")
    print("    disagreements (regex -> jev):", dict(Counter((gold[r["title_key"]], r["seniority_gated"]) for r in sdec
                                                     if r["seniority_gated"] != gold[r["title_key"]]).most_common(6)))
    write("posting_labels_sample.csv", recs)
    return ok


def cached(state):
    """True if jev_client.ask() already holds this exact request (same body hash), so it costs nothing."""
    body = json.dumps({"state": state, "model": MODEL, "questions": QUESTIONS},
                      sort_keys=True, ensure_ascii=False).encode("utf-8")
    return os.path.exists(os.path.join(CACHE, hashlib.sha256(body).hexdigest()[:24] + ".json"))


def projection():
    """(mean input tokens per request from the sample, n unlabeled titles not yet cached, projected $)."""
    ps = postings()
    unl, lab = title_sets(ps)
    toks = [t for _, _, a, t, _ in ask_all(sample_keys(lab), lab, "postings_sample") if a]
    mean = sum(toks) / max(1, len(toks))
    new = sum(not cached(state_for(unl[k])) for k in unl)
    return mean, new, mean * new * PRICE


# ---------------------------------------------------------------- label: the unlabeled set
KW_DIGITAL = re.compile(r"\b(it|ict|data|digital|software|developer|analytics?|informatics|automation|cyber\w*|"
                        r"sap|cloud|devops|network|infrastructure|ai|ml|systems?)\b")
KW_BIO = re.compile(r"\b(process|msat|cmc|bioprocess\w*|upstream|downstream|purification|scale up|"
                    r"tech(nology)? transfer|manufacturing science\w*)\b")


def run_label():
    mean, n, cost = projection()
    _, spent = layer_spent()
    print(f"projection: {n} uncached unlabeled titles x {mean:.0f} input tokens = ${cost:.3f}; layer spent so far "
          f"${spent:.4f}; budget ${BUDGET:.2f}")
    if spent + cost >= BUDGET:
        raise SystemExit("REFUSING: projected layer cost is over budget")
    ps = postings()
    unl, lab = title_sets(ps)
    res = ask_all(sorted(unl), unl, "postings_label")
    recs = [label_row(k, st, a, unl[k], "unlabeled") for k, st, a, _, e in res if a]
    skeys = set(sample_keys(lab))
    for k, st, a, _, e in ask_all(sorted(skeys), lab, "postings_sample"):
        if a:
            recs.append(label_row(k, st, a, lab[k], "validation_sample"))
    path = write("posting_labels.csv", recs)
    u = [r for r in recs if r["set"] == "unlabeled"]
    nposts = sum(r["n_postings"] for r in u)
    print(f"labeled {len(u)} unlabeled titles ({nposts} postings) + {len(recs) - len(u)} sample titles -> {path}")
    fam = Counter(r["job_family_gated"] for r in u)
    print("  job_family gated (titles):", dict(fam.most_common()))
    print("  job_family gated (postings):", dict(Counter({f: sum(r["n_postings"] for r in u if r["job_family_gated"] == f)
                                                          for f in fam}).most_common()))
    for col in ("digital_data_it", "bioprocess_core", "seniority"):
        print(f"  {col} gated:", dict(Counter(r[col + "_gated"] for r in u).most_common()))
    for col, kw in (("digital_data_it", KW_DIGITAL), ("bioprocess_core", KW_BIO)):
        dec = [r for r in u if r[col + "_gated"] != "unsure"]
        ag = sum((r[col + "_gated"] == "yes") == bool(kw.search(r["title_key"])) for r in dec)
        both = sum(r[col + "_gated"] == "yes" and bool(kw.search(r["title_key"])) for r in dec)
        yes = sum(r[col + "_gated"] == "yes" for r in dec)
        kwn = sum(bool(kw.search(r["title_key"])) for r in dec)
        print(f"  {col} vs keyword presence (decided titles): agree {pct(ag, len(dec))}; jev yes {yes}, "
              f"keyword {kwn}, both {both}")
    return recs


# ---------------------------------------------------------------- rollup (code only)
def load_labels():
    p = os.path.join(OUT, "posting_labels.csv")
    if not os.path.exists(p):
        raise SystemExit("no data/jev/posting_labels.csv yet: run `label` first")
    return {r["title_key"]: r for r in csv.DictReader(open(p, encoding="utf-8"))}


def resolve(r, labels):
    """(family bucket, digital bool, bioprocess bool, source). Regex label wins, else the gated Jev label."""
    if r["job_family"]:
        f = r["job_family"]
        return f, f in aiq.ORG_CORE, f in BIOPROCESS_REGEX, "regex"
    if r["excluded"]:
        return "excluded", False, False, ""
    lab = labels.get(title_key(r["role_title"]))
    if not lab:
        return "not_labeled", False, False, ""
    g = lab["job_family_gated"]
    return g, lab["digital_data_it_gated"] == "yes", lab["bioprocess_core_gated"] == "yes", "jev"


BUCKETS = FAMILY_NAMES + ["other", "unsure", "excluded", "not_labeled"]


def rollup_fields(first):
    return first + ["postings"] + [f"n_{b}" for b in BUCKETS] + [
        "n_digital_data_it", "n_bioprocess_core", "n_last_365d", "n_digital_data_it_365d",
        "n_bioprocess_core_365d", "newest_last_seen", "window_from", "family_from_regex", "family_from_jev", "model"]


def tally(group, labels, cutoff, window_from):
    rec = {"postings": len(group)}
    rec.update({f"n_{b}": 0 for b in BUCKETS})
    for k in ("n_digital_data_it", "n_bioprocess_core", "n_last_365d", "n_digital_data_it_365d",
              "n_bioprocess_core_365d", "family_from_regex", "family_from_jev"):
        rec[k] = 0
    for r in group:
        fam, dig, bio, src = resolve(r, labels)
        recent = (r["last_seen"] or "") >= cutoff
        rec[f"n_{fam}"] += 1
        rec["n_digital_data_it"] += dig
        rec["n_bioprocess_core"] += bio
        rec["n_last_365d"] += recent
        rec["n_digital_data_it_365d"] += dig and recent
        rec["n_bioprocess_core_365d"] += bio and recent
        rec["family_from_regex"] += src == "regex" and fam in FAMILY_NAMES
        rec["family_from_jev"] += src == "jev" and fam in FAMILY_NAMES
    rec["newest_last_seen"] = max((r["last_seen"] for r in group if r["last_seen"]), default="")
    rec["window_from"] = window_from
    rec["model"] = MODEL
    return rec


def run_rollup():
    ps, labels = postings(), load_labels()
    newest = max(r["last_seen"] for r in ps if r["last_seen"])
    start = datetime.date.fromisoformat(newest) - datetime.timedelta(days=WINDOW_DAYS)
    cutoff = start.isoformat()
    site_name = {s["site_id"]: s["site"] for s in rows("sites.csv")}
    by_site, by_acc, mirrors = defaultdict(list), defaultdict(list), Counter()
    for r in ps:
        by_acc[r["account_id"]].append(r)
        if r["site_id"]:
            by_site[r["site_id"]].append(r)
        if r["mirror_account_id"]:
            mirrors[r["mirror_account_id"]] += 1
    srecs = []
    for sid in sorted(by_site):
        grp = by_site[sid]
        srecs.append({"site_id": sid, "account_id": grp[0]["account_id"], "site": site_name.get(sid, ""),
                      **tally(grp, labels, cutoff, cutoff)})
    arecs = []
    for acc in sorted(by_acc):
        grp = by_acc[acc]
        arecs.append({"account_id": acc, "sites_with_postings": len({r["site_id"] for r in grp if r["site_id"]}),
                      "mirror_postings": mirrors.get(acc, 0), **tally(grp, labels, cutoff, cutoff)})
    sp = write("site_hiring.csv", srecs, rollup_fields(["site_id", "account_id", "site"]))
    ap = write("account_hiring.csv", arecs, rollup_fields(["account_id", "sites_with_postings", "mirror_postings"]))
    tot = Counter()
    for r in ps:
        tot[resolve(r, labels)[3] or resolve(r, labels)[0]] += 1
    fam_rows = Counter(resolve(r, labels)[0] for r in ps)
    labeled = sum(fam_rows[f] for f in FAMILY_NAMES)
    print(f"rollup: {len(ps)} postings after url dedupe; newest last_seen {newest}; window from {cutoff}")
    print(f"  family coverage: regex {sum(1 for r in ps if r['job_family'])} + jev "
          f"{sum(1 for r in ps if resolve(r, labels)[3] == 'jev' and resolve(r, labels)[0] in FAMILY_NAMES)} "
          f"= {labeled} ({labeled / len(ps):.1%}) of postings in a named family")
    print("  buckets:", dict(fam_rows.most_common()))
    print(f"  digital_data_it postings {sum(resolve(r, labels)[1] for r in ps)}  "
          f"bioprocess_core postings {sum(resolve(r, labels)[2] for r in ps)}")
    print(f"  -> {sp} ({len(srecs)} sites)\n  -> {ap} ({len(arecs)} accounts)")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    steps = {"sample": run_sample, "label": run_label, "rollup": run_rollup}
    if step not in steps and step != "all":
        print(__doc__)
        sys.exit(1)
    print("Jev usage before:", usage_summary())
    for s in (("sample", "label", "rollup") if step == "all" else (step,)):
        steps[s]()
    n, spent = layer_spent()
    print("Jev usage after:", usage_summary(), f"| this layer: {n} requests, ${spent:.4f}")

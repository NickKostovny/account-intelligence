#!/usr/bin/env python3
"""Jev pilot on the 32 postings that already have verified Tier-2 extracts.

Ground truth = data/posting_extracts.csv + data/posting_quotes.csv (Claude, verbatim-
verified). That makes every score below AGREEMENT with Claude, not accuracy: a Jev
pick Claude did not make is listed for a human to judge, never counted as wrong.

Nothing here writes to the SSOT tables. Output goes to data/pilot/ only.

  python3 pilot_jev.py regex     # site_stated + posted_date with plain code (no key)
  python3 pilot_jev.py probe     # 2 calls: does Jev bill the state once or per question?
  python3 pilot_jev.py tech      # Jev: which segments name tools, then which candidates are tools
  python3 pilot_jev.py autonomy  # Jev: autonomy_flag Choice + evidence segment
  python3 pilot_jev.py site      # Jev: site ICP conditions -> KEEP/FLAG/GAP/DROP + failed-condition tag
  python3 pilot_jev.py all
"""
import csv, datetime, json, os, re, sys
from concurrent.futures import ThreadPoolExecutor

import segment
from jev_client import ask, usage_summary

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "pilot")
WORKERS = 6


def rows(name):
    return list(csv.DictReader(open(os.path.join(HERE, "data", name), encoding="utf-8")))


EXTRACTS = {e["posting_id"]: e for e in rows("posting_extracts.csv")}
QUOTES = rows("posting_quotes.csv")


def write(name, recs):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    if recs:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
            w.writeheader()
            w.writerows(recs)
    return path


# ---------------------------------------------------------------- regex: site + date
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
AZ_LOC = re.compile(r"\bLocation\s+(.+?)\s+Job ID\b")
# multi-site AZ reqs list "City, Region, Country City, Region, Country ..."; the first is the primary
FIRST_LOC = re.compile(r"^(.+?, .+?, (?:United States|United Kingdom|South Korea|Puerto Rico|Czech Republic|"
                       r"New Zealand|Hong Kong|[A-Z][a-z]+))(?=\s|$)")
POSTED = re.compile(r"\s+Posted\s+\d{4}-\d{2}-\d{2}")
PTITLE = {r["posting_id"]: r["role_title"] for r in rows("postings.csv") if r["posting_id"] in EXTRACTS}
DATES = [
    (re.compile(r"\bDate [Pp]osted\s+(\d{1,2})/(\d{1,2})/(\d{4})"), lambda m: (m[3], m[2], m[1])),
    (re.compile(r"\bDate [Pp]osted\s+(\d{1,2})-([A-Za-z]{3})-(\d{4})"),
     lambda m: (m[3], MONTHS.get(m[2].lower()), m[1])),
    (re.compile(r"\bPosted\s+(\d{4})-(\d{2})-(\d{2})"), lambda m: (m[1], m[2], m[3])),
]


def regex_fields(body, title=""):
    site = ""
    loc = AZ_LOC.search(body)
    if loc:
        first = FIRST_LOC.match(loc.group(1).strip())
        site = first.group(1) if first else loc.group(1).strip()
    elif title and body.startswith(title):
        # Workday bodies open "<role title> <City, ST> Posted YYYY-MM-DD"
        m = POSTED.search(body, len(title))
        if m and m.start() - len(title) < 80:
            site = body[len(title):m.start()].strip()
    date = ""
    for rx, parts in DATES:
        m = rx.search(body)
        if m:
            y, mo, d = parts(m)
            try:
                date = datetime.date(int(y), int(mo), int(d)).isoformat()
            except (TypeError, ValueError):
                date = ""
            break
    return site, date


def run_regex():
    recs, site_ok, date_ok = [], 0, 0
    for pid, e in EXTRACTS.items():
        body = segment.load_body(pid)
        site, date = regex_fields(body, PTITLE.get(pid, ""))
        # same place, different wording ("Gaithersburg, MD") counts: compare the city
        s_ok = (site == e["site_stated"] == "") or \
            (bool(site) and site.split(",")[0].strip().lower() in e["site_stated"].lower())
        d_ok = date == e["posted_date_verbatim"]
        site_ok += s_ok
        date_ok += d_ok
        recs.append({"posting_id": pid, "site_regex": site, "site_claude": e["site_stated"], "site_agree": s_ok,
                     "date_regex": date, "date_claude": e["posted_date_verbatim"], "date_agree": d_ok})
    n = len(EXTRACTS)
    print(f"regex vs Claude: site_stated {site_ok}/{n}  posted_date {date_ok}/{n}  -> {write('regex_site_date.csv', recs)}")
    for r in recs:
        if not (r["site_agree"] and r["date_agree"]):
            print("  differs:", r["posting_id"], "| site", repr(r["site_regex"]), "vs", repr(r["site_claude"]),
                  "| date", r["date_regex"], "vs", r["date_claude"])


# ---------------------------------------------------------------- probe: billing shape
def run_probe():
    pid = next(iter(EXTRACTS))
    segs = segment.segments(segment.load_body(pid))
    doc = segment.document(segs)
    q = lambda i: {"type": "noul", "instructions": f"Does segment `{segs[i][0]}` name a software tool?"}
    one = ask(doc, {"q0": q(0)}, tag="probe1")
    ten = ask(doc, {f"q{i}": q(i) for i in range(10)}, tag="probe10")
    a, b = one["usage"]["input_tokens"], ten["usage"]["input_tokens"]
    print(f"model {one.get('model')}  body chars {len(doc)}  1 question: {a} input tokens  10 questions: {b}")
    print("state billed ONCE per request" if b < 3 * a else "state billed PER QUESTION -> keep states small")


# ---------------------------------------------------------------- tech
# same scope as Tier-2 rule 8 in gen_org_wf.py: named products AND system categories (LIMS, MES, CRM)
TECH_WHAT = ("a named software tool, platform, programming language, library, database or data system, "
             "cloud service, category of enterprise or lab system, or laboratory instrument or assay technique "
             "(for example Snowflake, Streamlit, Python, R, SQL, scikit-learn, JMP, SIMCA, OSI PI, Empower, "
             "Benchling, PowerPoint, LIMS, MES, ELN, ERP, CRM, historian, SAP, AWS, qPCR, flow cytometry, "
             "mass spectrometry)")
SEG_Q = {"type": "noul",
         "instructions": f"Does this text name at least one {TECH_WHAT}?",
         "criteria": {"true": "At least one tool, platform, language, system or lab technique is named.",
                      "false": "No tool is named. Company names, job titles, departments, therapy areas, "
                               "degrees and generic words like 'data', 'analytics' or 'digital' are not tools."}}
STOP = set("""a an and the of for to in on with by as at or our your you we they this that these those is are be
will role team teams experience skills ability knowledge strong working work including such using use""".split())
CAND = re.compile(r"[A-Za-z][A-Za-z0-9+#&.\-]*[A-Za-z0-9+#]|[A-Za-z]")
SLASHED = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:/[A-Za-z0-9+#]+)+")      # C/C++, CI/CD, OT/IoT kept whole too
PHRASE_BREAK = re.compile(r"[,;:()\[\]/–—•]|\s-\s")                      # a tool name never spans these


def candidates(text):
    """Every 1-3 word phrase that could be a tool name, as exact slices of the text."""
    toks = [(m.group(), m.start(), m.end()) for m in CAND.finditer(text)]
    out, seen = [], set()

    def add(phrase):
        if len(phrase) <= 40 and phrase.lower() not in seen:
            seen.add(phrase.lower())
            out.append(phrase)

    for m in SLASHED.finditer(text):
        add(m.group())
    for i in range(len(toks)):
        for n in (1, 2, 3):
            if i + n > len(toks):
                break
            chunk = toks[i:i + n]
            if chunk[0][0].lower() in STOP or chunk[-1][0].lower() in STOP:
                continue
            phrase = text[chunk[0][1]:chunk[-1][2]]
            if PHRASE_BREAK.search(phrase):
                continue
            looks = any(ch.isupper() for ch in phrase[1:]) or phrase[:1].isupper() or \
                re.search(r"[-+#0-9]|metry|graphy|omics|pcr|sequencing|assay|cytometry|computing", phrase, re.I)
            if looks:
                add(phrase)
    return out


def tool_q(phrase):
    return {"type": "noul",
            "instructions": {"phrase": phrase,
                             "question": f"In this text, is `phrase` itself the name of {TECH_WHAT}, used by "
                                         f"this role or team? Answer no if `phrase` is only part of a longer "
                                         f"tool name, or is a company, job title, department or generic word. "
                                         f"'Empowering' is not the tool Empower and 'R&D' is not the language R."}}


def norm(t):
    return re.sub(r"\s+", " ", t.strip().lower())


def run_tech(th_seg=0.0, th_tool=0.5):
    """th_seg=0 skips the segment gate: asking every candidate costs ~$0.10 for 32 postings,
    and the gate was the stage that lost most tools in the first run."""
    def one(pid):
        segs = segment.segments(segment.load_body(pid))
        flagged = []
        for sid, text, _, _ in segs:
            p = ask(text, {"has_tool": SEG_Q}, tag="tech_seg")["answers"]["has_tool"]["noul"] if th_seg else 1.0
            if p >= th_seg:
                flagged.append((sid, text, p))
        picks = []
        for sid, text, p in flagged:
            cands = candidates(text)
            if not cands:
                continue
            ans = {}
            for lo in range(0, len(cands), 60):          # 64k-token request cap: ~60 questions a request
                qs = {f"c{i}": tool_q(cands[i]) for i in range(lo, min(lo + 60, len(cands)))}
                ans.update(ask(text, qs, tag="tech_tool")["answers"])
            for i, c in enumerate(cands):
                pt = ans[f"c{i}"]["noul"]
                if pt >= th_tool:
                    picks.append((c, pt, sid))
        # of overlapping yes-phrases in one segment keep the most probable; a tie goes to the longer
        def beats(o, c):
            return o[2] == c[2] and norm(o[0]) != norm(c[0]) and \
                (norm(c[0]) in norm(o[0]) or norm(o[0]) in norm(c[0])) and \
                (o[1], len(o[0])) > (c[1], len(c[0]))
        keep = [c for c in picks if not any(beats(o, c) for o in picks)]
        return pid, len(segs), len(flagged), keep

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(one, EXTRACTS))
    recs, tp, fn, extra = [], 0, 0, 0
    for pid, nseg, nflag, keep in results:
        gold = {norm(t) for t in EXTRACTS[pid]["tech_stack"].split("|") if t.strip()}
        got = {}
        for c, p, sid in keep:
            got.setdefault(norm(c), (c, p, sid))
        for g in gold:
            hit = g in got or any(g in k or k in g for k in got)
            tp += hit
            fn += not hit
            recs.append({"posting_id": pid, "tool": g, "source": "claude", "jev_found": hit, "jev_p": "", "segment": ""})
        for k, (c, p, sid) in got.items():
            if not any(g == k or g in k or k in g for g in gold):
                extra += 1
                recs.append({"posting_id": pid, "tool": c, "source": "jev_only", "jev_found": True,
                             "jev_p": round(p, 3), "segment": sid})
    path = write("jev_tech.csv", recs)
    print(f"tech: Claude tools found by Jev {tp}/{tp + fn} ({tp / max(1, tp + fn):.0%} recall vs Claude)  "
          f"Jev-only picks for review {extra}  -> {path}")


# ---------------------------------------------------------------- autonomy
AUTONOMY_Q = {
    "type": "choice",
    "instructions": "Who owns this team's tools, data systems and methods, according to the text?",
    "criteria": {
        "site_autonomous": "The text shows this local site or team choosing or building its own tools and systems.",
        "central_owned": "The text shows a global, central or enterprise function owning the tools, standards or strategy that this role follows or sets across sites.",
        "mixed": "The text shows both: local choice and central ownership.",
        "not_stated": "The text does not say who owns the tools, systems or methods.",
    },
}


def run_autonomy():
    def one(pid):
        segs = segment.segments(segment.load_body(pid))
        doc = segment.document(segs)
        evidence = {"type": "choice",
                    "instructions": "Which segment best shows who owns this team's tools, systems or methods?",
                    "criteria": {sid: None for sid, _, _, _ in segs}}
        ans = ask(doc, {"autonomy": AUTONOMY_Q, "evidence": evidence}, tag="autonomy")["answers"]
        return pid, ans["autonomy"], ans["evidence"]

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(one, EXTRACTS))
    recs, agree, agree_conf, n_conf = [], 0, 0, 0
    for pid, a, ev in results:
        gold = EXTRACTS[pid]["autonomy_flag"] or "not_stated"
        ok = a["choice"] == gold
        agree += ok
        if a["confidence"] >= 0.9:
            n_conf += 1
            agree_conf += ok
        recs.append({"posting_id": pid, "claude": gold, "jev": a["choice"], "confidence": round(a["confidence"], 3),
                     "agree": ok, "evidence_segment": ev["choice"]})
    path = write("jev_autonomy.csv", recs)
    print(f"autonomy: agree with Claude {agree}/{len(results)}; at confidence>=0.9: {agree_conf}/{n_conf}  -> {path}")


# ---------------------------------------------------------------- site ICP verdict
# ICP at site level is NOT one yes/no: Jev answers atomic conditions, site_verdict() combines them
# in code, and the first condition that fails becomes the AE-visible tag.
# Ground truth = sites.csv verdict_auto (Claude, same pass that wrote the functions text), so the
# score is AGREEMENT. verdict_manual (7 rows, all Drop) overrides.
SITE_QS = {
    "status": {"type": "choice", "instructions": "What is the operating status of this site, according to the text?",
               "criteria": {
                   "operating": "The site runs today. Expansion work, or an acquisition of the site or its owner, does not change this.",
                   "planned_not_running": "The site is announced, planned, greenfield or under construction, and does not run yet.",
                   "closure_announced": "The text says the site will close or is being wound down.",
                   "closed": "The text says the site is closed or shut down.",
                   "sold_or_divested": "The text says this company sold or divested the site to another company.",
                   "not_stated": "The text does not say."}},
    "drug_substance": {"type": "noul", "instructions": "Does the text name drug substance manufacturing at this site?",
                       "criteria": {"true": "The text names drug substance, API, bulk, upstream, downstream, cell culture or biomanufacturing at this site.",
                                    "false": "The text names none of these."}},
    "pd_msat": {"type": "noul", "instructions": "Does the text name process development work at this site?",
                "criteria": {"true": "The text names process development, PD, MSAT, process science, CMC development, scale-up or tech transfer at this site.",
                             "false": "The text names none of these."}},
    "drug_product": {"type": "noul", "instructions": "Does the text name drug product work at this site?",
                     "criteria": {"true": "The text names drug product, formulation, fill-finish or aseptic filling at this site.",
                                  "false": "The text names none of these."}},
    "manufacturing": {"type": "noul", "instructions": "Does the text name manufacturing or production at this site?",
                      "criteria": {"true": "The text names manufacturing, production, GMP operations, commercial supply or commercial-scale manufacturing at this site.",
                                   "false": "The text names none of these."}},
    "support": {"type": "noul", "instructions": "Does the text name packaging, distribution, warehousing, a sales or commercial office, administration or training at this site?",
                "criteria": {"true": "The text names at least one of these support functions at this site.",
                             "false": "The text names none of these."}},
    "research": {"type": "noul", "instructions": "Does the text name research, discovery or R&D at this site?",
                 "criteria": {"true": "The text names research, discovery or R&D at this site.", "false": "The text names none of these."}},
    "modality": {"type": "choice", "instructions": "Which product types does this site make or develop?",
                 "criteria": {
                     "biologic_only": "Only biologics: antibodies, ADCs, proteins, vaccines, cell or gene therapy, mRNA or LNP, plasma products.",
                     "small_molecule_only": "Only small molecules: chemical APIs, oral solids, tablets, generics, chemical synthesis.",
                     "mixed": "Both biologic and small-molecule products.",
                     "not_stated": "The text does not say."}},
    "business": {"type": "choice", "instructions": "Which business does this site serve?",
                 "criteria": {
                     "human_pharma": "Medicines for people.",
                     "human_and_animal": "Both medicines for people and animal health products.",
                     "animal_health": "Only animal health or veterinary products.",
                     "diagnostics_or_devices": "Only diagnostics, instruments or medical devices.",
                     "consumer_or_nutrition": "Only consumer health, nutrition or cosmetics.",
                     "not_stated": "The text does not say."}},
    "cdmo": {"type": "noul", "instructions": "Does the text say this site makes or develops products for other companies under contract?",
             "criteria": {"true": "The text says CDMO, CMO, contract manufacturing or client projects at this site.",
                          "false": "The text does not say this."}},
}
# verdict words Claude left inside the text would leak the label
LEAK = re.compile(r"\b(KEEP|FLAG|GAP|DROP|DISQUALIFIED|Disqualified|disqualified|ICP[- ]confirm\w*|"
                  r"supplier[- ]not[- ]customer|PENDING-CLAY)\b")
NOUL_SURE, CHOICE_SURE = 0.2, 0.8        # a Noul is decisive at <=0.2 or >=0.8; a Choice at confidence >= 0.8


def person_names():
    names = set()
    for f, col in (("people.csv", "name"), ("site_heads.csv", "name"), ("site_heads.csv", "clay_name")):
        for r in rows(f):
            n = (r.get(col) or "").strip()
            if len(n.split()) >= 2 and len(n) < 60:
                names.add(n)
    return sorted(names, key=len, reverse=True)


def site_state(s, names):
    text = {"functions": s["functions"], "modality": s["modality"], "acquired_lineage": s["acquired_lineage"]}
    leaked = False
    for k, v in text.items():
        v2 = LEAK.sub("", v)
        leaked |= v2 != v
        for n in names:                      # nothing from our contact lists leaves this machine
            if n in v2:
                v2 = v2.replace(n, "[person]")
        text[k] = re.sub(r"\s{2,}", " ", v2).strip(" -—")
    state = {"company": s["account_id"], "site": s["site"], "city": s["city"], "region": s["region"], **text}
    return {k: v for k, v in state.items() if v}, leaked


def site_verdict(a):
    """(verdict, tag, uncertain_question). First failing condition wins; an unsure answer that the
    rule needs stops the chain with REVIEW."""
    def yes(q):
        p = a[q]["noul"]
        if NOUL_SURE < p < 1 - NOUL_SURE:
            raise LookupError(q)
        return p >= 0.5

    def pick(q):
        if a[q]["confidence"] < CHOICE_SURE:
            raise LookupError(q)
        return a[q]["choice"]

    def mass(q, options):
        return sum(a[q]["probabilities"].get(o, 0) for o in options)

    def any_yes(qs):
        """True if one condition is surely yes, False if all are surely no; unsure otherwise."""
        ps = {q: a[q]["noul"] for q in qs}
        if any(p >= 1 - NOUL_SURE for p in ps.values()):
            return True
        unsure = [q for q, p in ps.items() if p > NOUL_SURE]
        if unsure:
            raise LookupError(unsure[0])
        return False

    try:
        # "operating" vs "not stated" both mean carry on, so judge the probability MASS of the
        # two outcomes that change the verdict instead of the top option's confidence
        closed = mass("status", ("closed", "sold_or_divested", "closure_announced"))
        planned = mass("status", ("planned_not_running",))
        if closed >= CHOICE_SURE:
            return "CLOSED", "closed", ""
        if planned >= CHOICE_SURE:
            return "GAP", "greenfield", ""
        if closed + planned > 1 - CHOICE_SURE:
            raise LookupError("status")
        core = any_yes(("drug_substance", "pd_msat", "drug_product", "manufacturing"))
        if not core:
            if yes("support"):
                return "DROP", "support-only", ""
            return ("FLAG", "R&D-only", "") if yes("research") else ("FLAG", "unclear-mfg", "")
        # Nick 2026-09-30: animal health sites are KEEP like human medicines; diagnostics/consumer stay non-core
        if pick("business") in ("diagnostics_or_devices", "consumer_or_nutrition"):
            return "FLAG", "non-core", ""
        mod = pick("modality")
        if mod == "small_molecule_only":
            return "FLAG", "small-mol", ""
        if mod == "mixed":
            return "FLAG", "mixed", ""
        if mod == "not_stated":
            return "REVIEW", "modality?", "modality"
        if yes("cdmo"):                       # Nick 2026-09-28: CDMOs are customers
            return "KEEP", "CDMO", ""
        return "KEEP", "", ""
    except LookupError as e:
        return "REVIEW", f"{e.args[0]}?", e.args[0]


def run_site():
    sites, names = rows("sites.csv"), person_names()

    def one(s):
        state, leaked = site_state(s, names)
        return s, leaked, ask(state, SITE_QS, tag="site")["answers"]

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(one, sites))
    recs = []
    for s, leaked, a in results:
        gold = "DROP" if s["verdict_manual"].lower() == "drop" else s["verdict_auto"]
        # Nick 2026-09-28: the US June small-molecule re-pass applies to EU too (small-mol AND mixed -> FLAG),
        # and R&D-only offices are FLAG. Score against that policy; the SSOT is not changed here.
        policy_gold = gold
        v, tag, unsure = site_verdict(a)
        if s["region"] == "EU" and gold == "KEEP" and tag in ("small-mol", "mixed"):
            policy_gold = "FLAG"
        if gold == "DROP" and tag == "R&D-only":
            policy_gold = "FLAG"
        agree = v == policy_gold or (v == "CLOSED" and policy_gold in ("GAP", "DROP"))
        rec = {"site_id": s["site_id"], "account_id": s["account_id"], "region": s["region"],
               "short_text": len(s["functions"]) <= 40, "leaked_marker": leaked, "gold": gold,
               "policy_gold": policy_gold,
               "gold_source": "manual" if s["verdict_manual"] else "claude", "jev": v, "tag": tag,
               "unsure_on": unsure, "agree": agree,
               "eu_repass": s["region"] == "EU" and gold == "KEEP" and tag == "small-mol"}
        for q, ans in a.items():
            rec[q] = ans.get("choice", round(ans.get("noul", 0), 3))
            if "confidence" in ans:
                rec[q + "_conf"] = round(ans["confidence"], 3)
        recs.append(rec)
    path = write("jev_site_verdict.csv", recs)

    def report(label, rs):
        if not rs:
            return
        gated = [r for r in rs if r["jev"] != "REVIEW"]
        ag = sum(r["agree"] for r in gated)
        print(f"  {label:<28} n={len(rs):>3}  decided {len(gated):>3} ({len(gated)/len(rs):.0%})  "
              f"agree when decided {ag}/{len(gated)} ({ag/max(1,len(gated)):.0%})")

    print(f"site verdicts -> {path}   (agreement with Claude's site map under Nick's 2026-09-28 policy, not accuracy)")
    print(f"  map rows the new policy changes: {sum(r['gold'] != r['policy_gold'] for r in recs)}")
    clean = [r for r in recs if not r["short_text"] and not r["leaked_marker"]]
    report("all", recs)
    report("US, full text, no leak", [r for r in clean if r["region"] == "US"])
    report("EU, full text, no leak", [r for r in clean if r["region"] == "EU"])
    report("US short tag lists", [r for r in recs if r["short_text"]])
    report("had verdict words (stripped)", [r for r in recs if r["leaked_marker"]])
    report("manual verdicts", [r for r in recs if r["gold_source"] == "manual"])
    labels = ["KEEP", "FLAG", "GAP", "DROP", "CLOSED", "REVIEW"]
    print("  confusion (rows = Claude, cols = Jev):", "  ".join(f"{l:>6}" for l in labels))
    for g in ["KEEP", "FLAG", "GAP", "DROP"]:
        print(f"  {g:>38}", "  ".join(f"{sum(1 for r in recs if r['gold'] == g and r['jev'] == l):>6}" for l in labels))
    print("  tags:", dict(sorted(Counter(r["tag"] for r in recs if r["tag"]).items(), key=lambda x: -x[1])))
    print("  unsure on:", dict(Counter(r["unsure_on"] for r in recs if r["unsure_on"])))
    print("  EU small-mol KEEP rows Jev would FLAG (US got this re-pass in June, EU never did):",
          sum(r["eu_repass"] for r in recs))


if __name__ == "__main__":
    from collections import Counter
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    steps = {"regex": run_regex, "probe": run_probe, "tech": run_tech, "autonomy": run_autonomy, "site": run_site}
    if step == "all":
        for s in ("regex", "probe", "tech", "autonomy"):
            steps[s]()
    elif step in steps:
        steps[step]()
    else:
        print(__doc__)
        sys.exit(1)
    if step != "regex":
        print("Jev usage so far:", usage_summary())

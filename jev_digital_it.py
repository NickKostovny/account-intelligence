#!/usr/bin/env python3
"""Where digital, data and IT hiring sits, per account: EVIDENCE only, never a verdict on org design.

No text says "IT is centralized at X", so this builds location evidence from the job-postings
index. A posting counts as digital/data/IT by the postings layer rule (jev_postings.resolve():
regex family data_digital / automation_mes / lab_systems wins, else the gated Jev digital label),
minus one veto: a regex-digital title that Jev reads as NOT digital (Noul <= 0.2). The veto
exists because aiq.FAMILIES data_digital matches 'analytic' inside 'Analytical' / 'Bioanalytical',
and those QC lab roles sit at plants, so they would pull the location toward manufacturing sites.

Location per posting: site_id when the index resolved one, else the normalized city from city_raw
(labelled as a city), else -- only when the careers URL has no city at all -- the one site whose city
the title names, else remote / no location. Shares are over located postings. Counts, shares and labels are code:
  'concentrated at <location>'  top share >= 0.6 and located digital postings >= 8
  'spread over N locations'      located digital postings >= 8 otherwise
  'thin evidence'                located digital postings < 8, or located < 50% of digital postings

Jev (one request per unique digital role title, state = the title only, public text) answers:
global_scope (Noul: the title says global / enterprise / central), site_scope (Noul: the title
says one site or plant) and digital_data_it (the postings layer question, reused for the veto).
A Noul is shown only when decisive: >= 0.8 yes, <= 0.2 no, else 'unsure'.

Scores against keywords, Claude org units and the one Claude account hypothesis are AGREEMENT,
not accuracy. Nothing here writes to the SSOT tables. Output goes to data/jev/ only.

  python3 jev_digital_it.py scope     # Jev on every unique digital title (refuses if projection >= $1.00)
  python3 jev_digital_it.py locate    # code only: digital_it_location.csv + agreement report
  python3 jev_digital_it.py all
"""
import csv, datetime, hashlib, json, os, re, sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import aiq
import jev_postings as jp
from jev_client import CACHE, MODEL, ask, usage_summary

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "jev")
WORKERS = 6
BUDGET = 1.00                                   # dollars, whole layer
PRICE = 0.042 / 1e6                             # per input token
TAG = "digital_it_scope"
NOUL_SURE = 0.2
MIN_N, TOP_SHARE = 8, 0.6
MIN_COVERAGE = 0.5                              # located / digital; below it the top location is not shown
PROBE_N = 20                                    # titles asked first to measure tokens per request
WINDOW_DAYS = 365


def rows(name):
    return list(csv.DictReader(open(os.path.join(HERE, "data", name), encoding="utf-8")))


def gate(p):
    return "yes" if p >= 1 - NOUL_SURE else ("no" if p <= NOUL_SURE else "unsure")


# ---------------------------------------------------------------- questions
GLOBAL_Q = {
    "type": "noul",
    "instructions": "Does the job title `role_title` say the role is global, enterprise-wide, corporate or part "
                    "of a central function?",
    "criteria": {
        "true": "The title itself contains a scope word such as Global, Enterprise, Corporate, Worldwide, Central, "
                "Centre of Excellence (CoE), Global Business Services or Global Capability Centre.",
        "false": "The title has no such scope word. A title that names only a job, a site, a plant, a country or "
                 "a region such as Europe, EMEA, APAC or US is no.",
    },
}
SITE_Q = {
    "type": "noul",
    "instructions": "Does the job title `role_title` say the role serves one site, plant or local facility?",
    "criteria": {
        "true": "The title says the role belongs to a site, plant, factory, campus or local facility, for example "
                "Site IT Lead, Plant Automation Engineer or Local IT Support.",
        "false": "The title does not say this. A city or country name alone is no. Site reliability engineering "
                 "and website roles are no. A global, enterprise or corporate title is no.",
    },
}
QUESTIONS = {"global_scope": GLOBAL_Q, "site_scope": SITE_Q, "digital_data_it": jp.DIGITAL_Q}

KW_GLOBAL = re.compile(r"\b(global|enterprise|corporate|worldwide|central|coe|gbs|centre of excellence|"
                       r"center of excellence)\b")
KW_SITE = re.compile(r"\b(site|plant|local|facility|factory|campus)\b")


# ---------------------------------------------------------------- digital postings
def digital_groups(ps, labels):
    """{title_key: [rows]} for every posting the postings layer counts as digital/data/IT."""
    g = defaultdict(list)
    for r in ps:
        if jp.resolve(r, labels)[1]:
            g[jp.title_key(r["role_title"])].append(r)
    return g


def state_for(grp):
    return {"role_title": jp.state_for(grp)["role_title"]}


def cached(state):
    body = json.dumps({"state": state, "model": MODEL, "questions": QUESTIONS},
                      sort_keys=True, ensure_ascii=False).encode("utf-8")
    return os.path.exists(os.path.join(CACHE, hashlib.sha256(body).hexdigest()[:24] + ".json"))


def layer_spent():
    p = os.path.join(CACHE, "usage.jsonl")
    if not os.path.exists(p):
        return 0, 0.0
    rs = [json.loads(l) for l in open(p, encoding="utf-8")]
    rs = [r for r in rs if r.get("tag") == TAG]
    return len(rs), sum((r.get("usage") or {}).get("input_tokens", 0) for r in rs) * PRICE


def ask_all(keys, groups):
    def one(k):
        st = state_for(groups[k])
        try:
            resp = ask(st, QUESTIONS, tag=TAG)
            return k, st, resp["answers"], (resp.get("usage") or {}).get("input_tokens", 0), ""
        except Exception as e:                 # reported, not fatal; a rerun resumes from cache
            return k, st, None, 0, str(e)[:200]

    with ThreadPoolExecutor(WORKERS) as ex:
        out = list(ex.map(one, keys))
    errs = [o for o in out if o[4]]
    if errs:
        print(f"  {len(errs)} requests failed, first: {errs[0][4]}")
    return out


def pct(a, b):
    return f"{a}/{b} ({a / b:.0%})" if b else "0/0"


# ---------------------------------------------------------------- scope: Jev on digital titles
def run_scope():
    ps, labels = jp.postings(), jp.load_labels()
    groups = digital_groups(ps, labels)
    keys = sorted(groups)
    new = [k for k in keys if not cached(state_for(groups[k]))]
    _, spent = layer_spent()
    probe = ask_all(new[:PROBE_N], groups)
    toks = [t for _, _, a, t, _ in probe if a and t]
    mean = sum(toks) / len(toks) if toks else 400.0
    cost = mean * max(0, len(new) - PROBE_N) * PRICE
    print(f"scope: {len(keys)} unique digital titles, {len(new)} uncached; probe {len(toks)} x {mean:.0f} tokens; "
          f"projection ${cost:.4f}; layer spent before ${spent:.4f}; budget ${BUDGET:.2f}")
    if spent + cost >= BUDGET:
        raise SystemExit("REFUSING: projected layer cost is over budget")
    recs = []
    for k, st, a, _, e in ask_all(keys, groups):
        if not a:
            continue
        grp = groups[k]
        g, s, d = a["global_scope"]["noul"], a["site_scope"]["noul"], a["digital_data_it"]["noul"]
        src = "regex" if any(r["job_family"] for r in grp) else "jev"
        recs.append({
            "title_key": k, "role_title": st["role_title"], "n_postings": len(grp),
            "n_accounts": len({r["account_id"] for r in grp}), "digital_source": src,
            "regex_family": Counter(r["job_family"] for r in grp if r["job_family"]).most_common(1)[0][0]
            if src == "regex" else "",
            "global_scope_p": round(g, 3), "global_scope_gated": gate(g),
            "site_scope_p": round(s, 3), "site_scope_gated": gate(s),
            "digital_data_it_p": round(d, 3), "digital_data_it_gated": gate(d),
            "veto": "yes" if src == "regex" and gate(d) == "no" else "",
            "kw_global": int(bool(KW_GLOBAL.search(k))), "kw_site": int(bool(KW_SITE.search(k))),
            "model": MODEL,
        })
    path = jp.write("digital_title_scope.csv", recs)
    print(f"  wrote {len(recs)} titles -> {path}")
    report_scope(recs)
    return recs


def report_scope(recs):
    for col, kw in (("global_scope", "kw_global"), ("site_scope", "kw_site")):
        dec = [r for r in recs if r[col + "_gated"] != "unsure"]
        ag = sum((r[col + "_gated"] == "yes") == bool(int(r[kw])) for r in dec)
        yes = sum(r[col + "_gated"] == "yes" for r in dec)
        both = sum(r[col + "_gated"] == "yes" and int(r[kw]) for r in dec)
        print(f"  {col}: gated {dict(Counter(r[col + '_gated'] for r in recs))}; vs keyword presence (decided) "
              f"agree {pct(ag, len(dec))}; jev yes {yes}, keyword {sum(int(r[kw]) for r in dec)}, both {both}")
        print("    jev yes, no keyword:", [r["role_title"] for r in dec if r[col + "_gated"] == "yes"
                                           and not int(r[kw])][:6])
        print("    keyword, jev no:", [r["role_title"] for r in dec if r[col + "_gated"] == "no" and int(r[kw])][:6])
    rx = [r for r in recs if r["digital_source"] == "regex"]
    vet = [r for r in rx if r["veto"]]
    print(f"  digital_data_it on regex-digital titles: {dict(Counter(r['digital_data_it_gated'] for r in rx))}; "
          f"veto {len(vet)} titles / {sum(int(r['n_postings']) for r in vet)} postings")
    print("    veto by regex family:", dict(Counter(r["regex_family"] for r in vet)))
    print("    veto examples:", [r["role_title"] for r in vet[:8]])
    ana = [r for r in rx if re.search(r"analytical|bioanalyt", r["title_key"])]
    print(f"    'analytical' regex-digital titles vetoed {pct(sum(bool(r['veto']) for r in ana), len(ana))}")


def load_scope():
    p = os.path.join(OUT, "digital_title_scope.csv")
    if not os.path.exists(p):
        raise SystemExit("no data/jev/digital_title_scope.csv yet: run `scope` first")
    return {r["title_key"]: r for r in csv.DictReader(open(p, encoding="utf-8"))}


# ---------------------------------------------------------------- locations (code only)
REMOTE = re.compile(r"\b(remote|home ?based|field ?based|virtual|various|multiple locations|anywhere)\b")
CITY_ALIAS = {"lisboa": "lisbon", "bangalore": "bengaluru", "munchen": "munich", "koln": "cologne",
              "wien": "vienna", "praha": "prague", "milano": "milan", "bruxelles": "brussels",
              "beijing municipality": "beijing", "ciudad de mexico": "mexico city",
              "delegacion cuajimalpa de morelos": "mexico city"}   # Cuajimalpa is a Mexico City borough
COUNTRY = set(aiq._GEO_STOP) | {"unitedstates", "unitedkingdom", "unitedstatesofamerica", "australia", "taiwan",
                                "turkey", "argentina", "chile", "colombia", "greece", "hungary", "romania"}


def city_of(raw):
    """city_raw -> normalized city, 'remote', or ''. Workday 'Country - State - City' keeps the last part;
    'City, ST' keeps the first; url slugs lose their dashes."""
    s = re.sub(r"\([^)]*\)", " ", aiq.afold(raw or "")).lower().strip()
    if not s.strip(" -"):
        return ""
    if REMOTE.search(s):
        return "remote"
    s = s.split(" - ")[-1] if " - " in s else s.split(",")[0]
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", re.sub(r"[-_]+", " ", s))).strip()
    if s.replace(" ", "") in COUNTRY:          # a country alone is not a location
        return ""
    return CITY_ALIAS.get(s, s)


def site_keys():
    """{account_id: [(city key, site_id)]} from sites.csv city values, via aiq.city_keys()."""
    out = defaultdict(list)
    for s in rows("sites.csv"):
        primary, alias = aiq.city_keys(s["city"])
        out[s["account_id"]] += [(k, s["site_id"]) for k in primary | alias]
    return out


def title_site(r, keys):
    """site_id when the title names exactly one of the account's site cities (1-4 word run), else ''.
    Only used when the careers URL carries no city at all (UCB, CSL, AbbVie, J&J, Bayer hosts)."""
    w = jp.title_key(r["role_title"]).split()
    runs = {"".join(w[i:j]) for i in range(len(w)) for j in range(i + 1, min(len(w), i + 4) + 1)}
    hit = {sid for k, sid in keys.get(r["account_id"], []) if k in runs}
    return hit.pop() if len(hit) == 1 else ""


def location(r, site_city, keys):
    """(kind, id, display, source) with kind in site | city | remote | none; source url | title."""
    if r["site_id"]:
        return "site", r["site_id"], f"{site_city.get(r['site_id'], r['site_id'])} site", "url"
    c = city_of(r["city_raw"])
    if c == "remote":
        return "remote", "remote", "remote", "url"
    if c:
        return "city", "city:" + c, f"{c.title()} (city)", "url"
    sid = "" if r["city_slug"] else title_site(r, keys)
    if sid:
        return "site", sid, f"{site_city.get(sid, sid)} site", "title"
    return "none", "", "", ""


def label(n_loc, n_dig, top_share, top_name, n_places):
    if n_loc < MIN_N or n_loc < MIN_COVERAGE * n_dig:
        return "thin evidence"
    if top_share >= TOP_SHARE:
        return f"concentrated at {top_name}"
    return f"spread over {n_places} locations"


FIELDS = ["account_id", "company", "postings", "n_digital_layer", "n_vetoed", "n_digital", "n_digital_365d",
          "n_located", "n_located_from_title", "n_remote", "n_no_location", "n_locations", "n_sites", "n_cities", "top_location",
          "top_kind", "top_id", "top_n", "top_share", "top3_share", "location_label", "location_shares",
          "n_global_titled", "n_global_unsure", "global_titled_share", "global_scope_p_mean",
          "global_titled_display", "top_global_titled_share", "n_site_titled", "site_titled_share",
          "site_scope_p_mean", "mirror_digital", "newest_last_seen", "caveat", "model"]


def account_row(acc, company, allp, dig, scope, site_city, keys, cutoff, mirror_n):
    rec = {f: "" for f in FIELDS}
    rec.update({"account_id": acc, "company": company, "postings": len(allp), "mirror_digital": mirror_n})
    if not allp:
        rec.update({"location_label": "no postings indexed", "caveat": "no postings indexed"})
        return rec
    layer = [r for r in dig]
    keep = [r for r in layer if not scope.get(jp.title_key(r["role_title"]), {}).get("veto")]
    locs = [(r, location(r, site_city, keys)) for r in keep]
    placed = [(r, l) for r, l in locs if l[0] in ("site", "city")]
    by = Counter(l[1] for _, l in placed)
    name = {l[1]: (l[0], l[2]) for _, l in placed}
    ranked = sorted(by.items(), key=lambda x: (-x[1], x[0]))
    n_loc = len(placed)
    top_id, top_n = ranked[0] if ranked else ("", 0)
    top_share = top_n / n_loc if n_loc else 0.0
    sc = [scope.get(jp.title_key(r["role_title"])) for r in keep]
    sc = [s for s in sc if s]
    g_yes = sum(s["global_scope_gated"] == "yes" for s in sc)
    s_yes = sum(s["site_scope_gated"] == "yes" for s in sc)
    top_rows = [r for r, l in placed if l[1] == top_id]
    top_sc = [scope[jp.title_key(r["role_title"])] for r in top_rows if jp.title_key(r["role_title"]) in scope]
    rec.update({
        "n_digital_layer": len(layer), "n_vetoed": len(layer) - len(keep), "n_digital": len(keep),
        "n_digital_365d": sum((r["last_seen"] or "") >= cutoff for r in keep),
        "n_located": n_loc, "n_located_from_title": sum(l[3] == "title" for _, l in placed), "n_remote": sum(l[0] == "remote" for _, l in locs),
        "n_no_location": sum(l[0] == "none" for _, l in locs), "n_locations": len(by),
        "n_sites": sum(name[k][0] == "site" for k in by), "n_cities": sum(name[k][0] == "city" for k in by),
        "top_location": name[top_id][1] if top_id else "", "top_kind": name[top_id][0] if top_id else "",
        "top_id": top_id, "top_n": top_n, "top_share": round(top_share, 3),
        "top3_share": round(sum(n for _, n in ranked[:3]) / n_loc, 3) if n_loc else "",
        "location_label": label(n_loc, len(keep), top_share, name[top_id][1] if top_id else "", len(by)) if keep
        else "no digital postings",
        "location_shares": json.dumps([{"loc": name[k][1], "kind": name[k][0], "id": k, "n": n,
                                        "share": round(n / n_loc, 3)} for k, n in ranked[:10]]),
        "n_global_titled": g_yes, "n_global_unsure": sum(s["global_scope_gated"] == "unsure" for s in sc),
        "global_titled_share": round(g_yes / len(keep), 3) if keep else "",
        "global_scope_p_mean": round(sum(float(s["global_scope_p"]) for s in sc) / len(sc), 3) if sc else "",
        "global_titled_display": f"{g_yes / len(keep):.0%}" if len(keep) >= MIN_N else "",
        "top_global_titled_share": round(sum(s["global_scope_gated"] == "yes" for s in top_sc) / len(top_sc), 3)
        if top_sc else "",
        "n_site_titled": s_yes, "site_titled_share": round(s_yes / len(keep), 3) if keep else "",
        "site_scope_p_mean": round(sum(float(s["site_scope_p"]) for s in sc) / len(sc), 3) if sc else "",
        "newest_last_seen": max((r["last_seen"] for r in keep if r["last_seen"]), default=""),
        "model": MODEL if keep else "",
    })
    cav = []
    if keep and rec["n_no_location"] + rec["n_remote"]:
        cav.append(f"no location on {rec['n_no_location'] + rec['n_remote']} of {len(keep)}")
    if rec["n_located_from_title"]:
        cav.append(f"{rec['n_located_from_title']} located from a site city in the title")
    if mirror_n:
        cav.append(f"{mirror_n} digital reqs on another account's careers host map here")
    if not keep:
        cav.append("no digital postings")
    rec["caveat"] = "; ".join(cav)
    return rec


def run_locate():
    ps, labels, scope = jp.postings(), jp.load_labels(), load_scope()
    newest = max(r["last_seen"] for r in ps if r["last_seen"])
    cutoff = (datetime.date.fromisoformat(newest) - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    site_city = {s["site_id"]: s["city"] for s in rows("sites.csv")}
    keys = site_keys()
    allp, dig, mirror = defaultdict(list), defaultdict(list), Counter()
    for r in ps:
        allp[r["account_id"]].append(r)
        if jp.resolve(r, labels)[1]:
            dig[r["account_id"]].append(r)
            if r["mirror_account_id"] and not scope.get(jp.title_key(r["role_title"]), {}).get("veto"):
                mirror[r["mirror_account_id"]] += 1
    accs = rows("accounts.csv")
    recs = [account_row(a["account_id"], a["company"], allp.get(a["account_id"], []), dig.get(a["account_id"], []),
                        scope, site_city, keys, cutoff, mirror.get(a["account_id"], 0)) for a in accs]
    recs.sort(key=lambda r: (-(r["n_digital"] or 0) if r["n_digital"] != "" else 1, r["account_id"]))
    path = jp.write("digital_it_location.csv", recs, FIELDS)
    print(f"locate: {len(recs)} accounts -> {path}; window from {cutoff}")
    print(f"  {'account':<22} {'dig':>5} {'loc':>5} {'top':>5}  {'global':>6}  label")
    for r in recs:
        if r["n_digital"]:
            print(f"  {r['account_id']:<22} {r['n_digital']:>5} {r['n_located']:>5} {r['top_share']:>5}  "
                  f"{r['global_titled_display'] or '-':>6}  {r['location_label']}  [{r['caveat']}]")
    print("  labels:", dict(Counter(r["location_label"].split(" at ")[0].split(" over ")[0] for r in recs)))
    agreement(recs)
    return recs


# ---------------------------------------------------------------- agreement with what already exists
def agreement(recs):
    ah = {r["account_id"]: r for r in csv.DictReader(open(os.path.join(OUT, "account_hiring.csv"), encoding="utf-8"))}
    ok = sum(int(ah[r["account_id"]]["n_digital_data_it"]) == r["n_digital_layer"] for r in recs if r["account_id"] in ah)
    print(f"  reconcile n_digital_layer vs account_hiring.n_digital_data_it: {ok}/{len(ah)} accounts equal")
    by = {r["account_id"]: r for r in recs}
    units = defaultdict(Counter)
    for u in rows("org_units.csv"):
        if u["site_id"] and set(u["job_families"].split("|")) & aiq.ORG_CORE:
            units[u["account_id"]][u["site_id"]] += 1
    for acc, c in sorted(units.items()):
        r = by.get(acc, {})
        top_u = c.most_common(1)[0][0]
        shares = {d["id"]: d["share"] for d in json.loads(r.get("location_shares") or "[]")}
        print(f"  org_units (Claude Tier-2) {acc}: digital units by site {dict(c)}; modal {top_u}; this layer top "
              f"{r.get('top_id')} -> {'agree' if top_u == r.get('top_id') else 'differ'} (modal site share here "
              f"{shares.get(top_u, 0)})")
    ext = {e["posting_id"]: e["autonomy_flag"] for e in rows("posting_extracts.csv") if e["autonomy_flag"]}
    scope = load_scope()
    pairs = []
    for r in rows("postings.csv"):
        s = scope.get(jp.title_key(r["role_title"])) if r["posting_id"] in ext else None
        if s:
            pairs.append((ext[r["posting_id"]], s["global_scope_gated"]))
    print(f"  Claude autonomy_flag (body) vs global_scope (title) on {len(pairs)} extracted digital postings:",
          dict(Counter(pairs)))
    for a in rows("accounts.csv"):
        if a["it_centralization_hypothesis"]:
            r = by.get(a["account_id"], {})
            print(f"  Claude hypothesis {a['account_id']} ({a['it_centralization_confidence']}): "
                  f"{a['it_centralization_hypothesis'][:110]}... | this layer: {r.get('location_label')}, "
                  f"global-titled {r.get('global_titled_display')}, site-titled {r.get('site_titled_share')}")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    steps = {"scope": run_scope, "locate": run_locate}
    if step not in steps and step != "all":
        print(__doc__)
        sys.exit(1)
    print("Jev usage before:", usage_summary())
    for s in (("scope", "locate") if step == "all" else (step,)):
        steps[s]()
    n, spent = layer_spent()
    print("Jev usage after:", usage_summary(), f"| this layer: {n} requests, ${spent:.4f}")

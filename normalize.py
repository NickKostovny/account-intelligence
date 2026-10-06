#!/usr/bin/env python3
"""
Normalize Invert's source CSVs into the 4-table SSOT:
  data/accounts.csv, data/sites.csv, data/people.csv, data/signals.csv

Sources (in ~/Downloads):
  - master account universe:  "2026 Q2-Q4 Account List - Top 50 Bio - FINAL.csv"
  - US/global site spine:     "Latest site data 6_5  - Invert-Site-Mapping-49accts-COMPLETE.csv"
  - EU site spine:            "EU-Site-Mapping-56accts-COMPLETE.csv"

Handles: blank banner row, "Coverage Summary" appendix, account-name aliases,
US/EU overlap dedupe, verdict->icp_fit mapping, people seeding from named site heads.
Re-runnable and idempotent (fully rewrites the data/ files).
"""
import csv, os, re, sys
from collections import Counter, OrderedDict

DL = "/Users/nickkostovny/Downloads"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

MASTER = os.path.join(DL, "2026 Q2-Q4 Account List - Top 50 Bio - FINAL.csv")
SITE_FILES = [
    ("US", os.path.join(DL, "Latest site data 6_5  - Invert-Site-Mapping-49accts-COMPLETE.csv")),
    ("EU", os.path.join(DL, "EU-Site-Mapping-56accts-COMPLETE.csv")),
]

# --- account-name canonicalization -------------------------------------------
ALIASES = {
    "j&j": "Johnson & Johnson", "j&j (janssen)": "Johnson & Johnson",
    "bms": "Bristol Myers Squibb",
    "merck & co": "Merck and Co", "merck & co (msd)": "Merck and Co",
    "gilead": "Gilead Sciences",
    "genentech (roche)": "Genentech",
    "alexion (az rare disease)": "Alexion",
    "beigene (beone)": "BeiGene", "beigene": "BeiGene",
    "fujifilm biotechnologies": "Fujifilm", "fujifilm diosynth biotechnologies": "Fujifilm",
    "merck kgaa / milliporesigma": "Merck KGaA",
    "seagen (now pfizer)": "Seagen",
    "alnylam pharmaceuticals": "Alnylam",
    "waters": "Waters Corporation",
}
# junk tokens that appear in the appendix / verdict-legend mini-tables
JUNK = {
    "account", "keep", "drop", "flag", "gap", "total site nodes", "nodes by verdict",
    "geographies", "before outbound", "verdict legend",
}

def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", norm(s)).strip("-")

# --- read master account list -------------------------------------------------
def read_master():
    with open(MASTER, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    accounts = OrderedDict()
    canon_by_norm = {}          # normalized name -> canonical company
    for r in rows:
        company = (r.get("Company") or "").strip()
        if not company:
            continue
        aid = slug(company)
        exclude = (r.get("Exclude") or "").strip().lower() == "x"
        accounts[aid] = {
            "account_id": aid,
            "company": company,
            "in_universe": "true",
            "active": "false" if exclude else "true",
            "exclude": "true" if exclude else "false",
            "hq": (r.get("HQ") or "").strip(),
            "est_revenue": (r.get("Est. Pharma Revenue") or "").strip(),
            "primary_modalities": (r.get("Primary Modalities") or "").strip(),
            "icp_fit_company": (r.get("ICP Fit") or "").strip(),
            "pipeline_status": (r.get("Invert Pipeline Status") or "").strip(),
            "deal_owner": (r.get("Deal Owner") or "").strip(),
            "key_invert_contact": (r.get("Key Invert Contact") or "").strip(),
            "notes": (r.get("Notes") or "").strip(),
            # enrichment fields (populated by later tracks)
            "modality_footprint": "", "lineage_summary": "",
            "digital_data_it_location": "", "digital_data_it_confidence": "",
            "it_centralization_hypothesis": "", "it_centralization_confidence": "",
            "decision_power_hypothesis": "", "decision_power_confidence": "",
            "company_news_latest": "", "last_refreshed": "",
        }
        canon_by_norm[norm(company)] = company
    return accounts, canon_by_norm

def canonicalize(raw, canon_by_norm):
    n = norm(raw)
    if n in ALIASES:
        return ALIASES[n]
    if n in canon_by_norm:
        return canon_by_norm[n]
    # strip parentheticals then retry
    stripped = norm(re.sub(r"\(.*?\)", "", raw))
    if stripped in ALIASES:
        return ALIASES[stripped]
    if stripped in canon_by_norm:
        return canon_by_norm[stripped]
    # drop common corporate suffixes and retry
    base = re.sub(r"\b(pharmaceuticals|pharma|inc|corporation|corp|ag|plc|ltd)\b", "", stripped).strip()
    for cn, comp in canon_by_norm.items():
        cbase = re.sub(r"\b(pharmaceuticals|pharma|inc|corporation|corp|ag|plc|ltd)\b", "", cn).strip()
        if base and base == cbase:
            return comp
    return raw.strip()  # out-of-universe company, kept as-is

# --- verdict -> icp_fit --------------------------------------------------------
def icp_from_verdict(v):
    t = norm(v)
    if t.startswith("keep"): return "yes"
    if t.startswith("drop"): return "no"
    if t.startswith("flag"): return "soft"
    if t.startswith("gap"):  return "soft"
    return "unknown"

# --- read a site CSV, truncating appendix -------------------------------------
def read_sites(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    hidx = next(i for i, r in enumerate(rows[:6])
                if "Account" in [c.strip() for c in r] and "Site" in [c.strip() for c in r])
    header = [c.strip() for c in rows[hidx]]
    idx = {name: i for i, name in enumerate(header)}
    out = []
    for r in rows[hidx + 1:]:
        if not any(c.strip() for c in r):
            continue
        acct = r[idx["Account"]].strip() if len(r) > idx["Account"] else ""
        # stop at the appendix
        if norm(acct).startswith("coverage summary") or norm(acct).startswith("accounts covered") \
           or norm(acct).startswith("per-account") or norm(acct).startswith("avg pulls") \
           or norm(acct).startswith("scoped clay") or norm(acct).startswith("original projection") \
           or norm(acct).startswith("rows with a named") or norm(acct).startswith("corrections captured") \
           or norm(acct).startswith("known limits") or norm(acct).startswith("method"):
            break
        if not acct or norm(acct) in JUNK:
            continue
        site = r[idx["Site"]].strip() if len(r) > idx["Site"] else ""
        if not site:
            continue
        def g(col):
            return r[idx[col]].strip() if col in idx and len(r) > idx[col] else ""
        out.append({
            "account_raw": acct, "site": site, "pipeline": g("Pipeline"),
            "city": g("City"), "metro": g("Metro"),
            "functions": g("Functions"), "modality": g("Modality"),
            "verdict_auto": g("Verdict (AUTO GEN)"),
            "verdict_manual": g("MANUAL VERDICT CONFIRM"),
            "verdict_manual_reason": g("MANUAL VERDICT CONFIRM - REASON"),
            "ae": g("AE"), "named_site_head": g("Named Site Head"),
            "evidence_titles": g("Evidence (titles)"), "source": g("Source"),
        })
    return out

# --- main ---------------------------------------------------------------------
def main():
    accounts, canon_by_norm = read_master()

    sites = []
    seen = {}          # dedup key -> index in sites
    dropped_dupes = 0
    extra_companies = OrderedDict()

    for region, path in SITE_FILES:
        for s in read_sites(path):
            company = canonicalize(s["account_raw"], canon_by_norm)
            aid = slug(company)
            if aid not in accounts and aid not in extra_companies:
                extra_companies[aid] = company  # out-of-universe (mostly EU-only)
            key = (aid, norm(re.sub(r"\(.*?\)", "", s["site"]))[:60], norm(s["city"]))
            row = {
                "site_id": "",  # assigned after dedup
                "account_id": aid, "region": region,
                "site": s["site"], "city": s["city"], "metro": s["metro"],
                "legal_entity": "", "acquired_lineage": "",
                "functions": s["functions"], "modality": s["modality"],
                "icp_fit": icp_from_verdict(s["verdict_manual"] or s["verdict_auto"]),
                "icp_fit_reason": s["verdict_manual_reason"],
                "verdict_auto": s["verdict_auto"], "verdict_manual": s["verdict_manual"],
                "ae": s["ae"], "named_site_head": s["named_site_head"],
                "evidence_titles": s["evidence_titles"], "source": s["source"],
                "decision_power_hypothesis": "", "decision_power_confidence": "",
                "site_news_latest": "", "last_refreshed": "",
            }
            if key in seen:
                dropped_dupes += 1
                # keep the more complete row (prefer one with a named head)
                ex = sites[seen[key]]
                if not ex["named_site_head"] and row["named_site_head"]:
                    row["site_id"] = ex["site_id"]
                    sites[seen[key]] = row
                continue
            seen[key] = len(sites)
            sites.append(row)

    # register out-of-universe companies in accounts
    for aid, company in extra_companies.items():
        accounts[aid] = {
            "account_id": aid, "company": company,
            "in_universe": "false", "active": "false", "exclude": "false",
            "hq": "", "est_revenue": "", "primary_modalities": "",
            "icp_fit_company": "", "pipeline_status": "", "deal_owner": "",
            "key_invert_contact": "", "notes": "Added from site-map (out of master 50 universe)",
            "modality_footprint": "", "lineage_summary": "",
            "digital_data_it_location": "", "digital_data_it_confidence": "",
            "it_centralization_hypothesis": "", "it_centralization_confidence": "",
            "decision_power_hypothesis": "", "decision_power_confidence": "",
            "company_news_latest": "", "last_refreshed": "",
        }

    # assign stable site_ids (account slug + zero-padded counter within account)
    per_acct = Counter()
    for row in sites:
        if row["site_id"]:
            continue
        per_acct[row["account_id"]] += 1
        row["site_id"] = f'{row["account_id"]}--s{per_acct[row["account_id"]]:02d}'

    # seed people from named site heads
    people = []
    pcount = 0
    for row in sites:
        head = row["named_site_head"].strip()
        if not head or norm(head) in {"none", "n/a", "none identified", "-"}:
            continue
        # split "Name - Title" if present
        title = ""
        m = re.split(r"\s+[-–—]\s+", head, maxsplit=1)
        name = m[0].strip()
        if len(m) > 1:
            title = m[1].strip()
        pcount += 1
        people.append({
            "person_id": f'p{pcount:04d}',
            "account_id": row["account_id"], "site_id": row["site_id"],
            "name": name, "title": title, "dept": "", "seniority": "",
            "linkedin_url": "", "email": "", "persona": "",
            "why_champion": "", "surfaced_via": "site-map",
            "source_url": row["source"], "last_activity": "",
        })

    # --- write ----------------------------------------------------------------
    def write(name, rows, fields):
        with open(os.path.join(DATA, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

    acct_fields = list(next(iter(accounts.values())).keys())
    write("accounts.csv", list(accounts.values()), acct_fields)
    write("sites.csv", sites, list(sites[0].keys()))
    people_fields = ["person_id","account_id","site_id","name","title","dept","seniority",
                     "linkedin_url","email","persona","why_champion","surfaced_via","source_url","last_activity"]
    write("people.csv", people, people_fields)
    signal_fields = ["signal_id","account_id","site_id","signal_type","title","date",
                     "named_people","topic","pain_hypothesis","confidence","url","source","captured_at"]
    write("signals.csv", [], signal_fields)

    # --- integrity report -----------------------------------------------------
    aids = set(accounts.keys())
    bad_fk = [s["site_id"] for s in sites if s["account_id"] not in aids]
    print("=== SSOT build complete ===")
    print(f"accounts: {len(accounts)}  (in-universe: {sum(1 for a in accounts.values() if a['in_universe']=='true')}, "
          f"active: {sum(1 for a in accounts.values() if a['active']=='true')})")
    print(f"sites:    {len(sites)}  (US+EU merged; {dropped_dupes} duplicate rows collapsed)")
    print(f"people:   {len(people)}  (seeded from named site heads)")
    print(f"signals:  0  (schema only)")
    print(f"FK check: {len(bad_fk)} orphan site rows (should be 0)")
    print(f"out-of-universe companies added from site-map: {len(extra_companies)}")
    print("  ", ", ".join(extra_companies.values()))
    icp = Counter(s["icp_fit"] for s in sites)
    print("site icp_fit distribution:", dict(icp))
    # accounts with most sites
    sc = Counter(s["account_id"] for s in sites)
    print("top accounts by site count:", sc.most_common(8))

if __name__ == "__main__":
    main()

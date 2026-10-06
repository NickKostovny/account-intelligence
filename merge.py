#!/usr/bin/env python3
"""
Merge the enrichment workflow's JSON output into the SSOT CSVs.
Usage: python3 merge.py <enrich_out.json>
- pilot.* -> AstraZeneca account fields + sites + signals + people
- publications_by_account[] -> per-account publication signals + author people
Idempotent-ish: dedupes signals by (account_id, signal_type, key) and people by (account_id, name).
Re-runnable: reloads current SSOT, appends only new rows.
"""
import csv, os, sys, re, json, datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TODAY = datetime.date.today().isoformat()

SIG_FIELDS = ["signal_id","account_id","site_id","signal_type","title","date","named_people",
              "topic","pain_hypothesis","tools_named","location","confidence","url","source","captured_at"]
PEOPLE_FIELDS = ["person_id","account_id","site_id","name","title","dept","seniority",
                 "linkedin_url","email","persona","why_champion","surfaced_via","source_url","last_activity"]

def load(name):
    p = os.path.join(DATA, name)
    return list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.exists(p) else []

def write(name, rows, fields):
    with open(os.path.join(DATA, name), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in fields})

def ncity(c):
    if not c: return ""
    c = re.sub(r"\(.*?\)", "", c)
    c = c.split(",")[0].split("/")[0]
    return re.sub(r"[^a-z0-9]", "", c.lower())

def nname(n):
    return re.sub(r"[^a-z]", "", (n or "").lower())

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "enrich_out.json")
    data = json.load(open(path, encoding="utf-8"))

    accounts = load("accounts.csv"); acc_by_id = {a["account_id"]: a for a in accounts}
    sites = load("sites.csv")
    people = load("people.csv")
    signals = load("signals.csv")

    # city -> site_id index per account
    city_idx = defaultdict(dict)
    for s in sites:
        for cand in {ncity(s.get("city","")), ncity(s.get("metro",""))}:
            if cand: city_idx[s["account_id"]].setdefault(cand, s["site_id"])

    def site_for(aid, matched_city):
        return city_idx.get(aid, {}).get(ncity(matched_city), "")

    # existing dedupe keys (title+url combined so distinct talks sharing one agenda URL both survive)
    def dkey(title, url):
        return (title or "").strip().lower()[:80] + "|" + (url or "").strip().lower()[:120]
    sig_keys = set((s["account_id"], s.get("signal_type",""),
                    dkey(s.get("title",""), s.get("url",""))) for s in signals)
    ppl_keys = set((p["account_id"], nname(p.get("name",""))) for p in people)
    sig_n = len(signals); ppl_n = len(people)

    added = defaultdict(int)

    def add_signal(aid, stype, rec, site_id=""):
        nonlocal sig_n
        key = (aid, stype, dkey(rec.get("title",""), rec.get("url","")))
        if not rec.get("title") or key in sig_keys: return
        sig_keys.add(key); sig_n += 1
        signals.append({
            "signal_id": f"sig{sig_n:05d}", "account_id": aid,
            "site_id": site_id or site_for(aid, rec.get("matched_city","")),
            "signal_type": stype, "title": rec.get("title",""),
            "date": rec.get("date") or rec.get("year") or rec.get("posted_date",""),
            "named_people": "|".join(rec.get("named_people") or rec.get("authors_named") or []),
            "topic": rec.get("topic") or " ".join(x for x in [rec.get("dept",""), rec.get("seniority","")] if x),
            "pain_hypothesis": rec.get("pain_hypothesis",""),
            "tools_named": "|".join(rec.get("tools_named") or []),
            "location": rec.get("location",""),
            "confidence": rec.get("confidence",""),
            "url": rec.get("url",""), "source": rec.get("source",""), "captured_at": TODAY,
        })
        added[stype] += 1

    def add_person(aid, name, **kw):
        nonlocal ppl_n
        if not name: return
        key = (aid, nname(name))
        if key in ppl_keys: return
        ppl_keys.add(key); ppl_n += 1
        row = {"person_id": f"p{ppl_n:04d}", "account_id": aid, "site_id": kw.get("site_id",""),
               "name": name, "title": kw.get("title",""), "dept": "", "seniority": "",
               "linkedin_url": "", "email": "", "persona": kw.get("persona",""),
               "why_champion": kw.get("why_champion",""), "surfaced_via": kw.get("surfaced_via",""),
               "source_url": kw.get("source_url",""), "last_activity": ""}
        people.append(row); added["people"] += 1

    def ingest_pubs(aid, pubs):
        for p in pubs or []:
            sid = site_for(aid, p.get("matched_city",""))
            add_signal(aid, "publication", p, site_id=sid)
            for au in (p.get("authors_named") or [])[:4]:
                add_person(aid, au, title="", persona="SME (publication)",
                           why_champion=p.get("topic",""), surfaced_via="publication",
                           source_url=p.get("url",""), site_id=sid)

    # ---- pilot ----
    P = data.get("pilot", {})
    aid = "astrazeneca"
    if aid in acc_by_id:
        a = acc_by_id[aid]
        lin = P.get("lineage", {}) or {}
        if lin.get("lineage_summary"): a["lineage_summary"] = lin["lineage_summary"]
        if lin.get("modality_footprint"): a["modality_footprint"] = lin["modality_footprint"]
        dec = P.get("decision", {}) or {}
        for k in ["it_centralization_hypothesis","it_centralization_confidence",
                  "digital_data_it_location","decision_power_hypothesis","decision_power_confidence"]:
            if dec.get(k): a[k] = dec[k]
        jobs = P.get("jobs", {}) or {}
        if not a.get("digital_data_it_location") and jobs.get("digital_data_it_proxy"):
            a["digital_data_it_location"] = jobs["digital_data_it_proxy"]
        if jobs.get("digital_data_it_confidence"):
            a["digital_data_it_confidence"] = jobs["digital_data_it_confidence"]
        # decision evidence -> append to hypothesis display via evidence not stored separately; keep concise
        a["last_refreshed"] = TODAY

        # site legal entity + lineage onto sites
        site_by_city = {}
        for s in sites:
            if s["account_id"] == aid:
                site_by_city[ncity(s.get("city",""))] = s
        for le in lin.get("site_legal_entities", []) or []:
            s = site_by_city.get(ncity(le.get("matched_city","")))
            if s and le.get("legal_entity"): s["legal_entity"] = le["legal_entity"]
        for acq in lin.get("acquisitions", []) or []:
            s = site_by_city.get(ncity(acq.get("site_city","")))
            if s:
                note = f'{acq.get("acquired","")} ({acq.get("year","")}): {acq.get("note","")}'.strip()
                s["acquired_lineage"] = (s.get("acquired_lineage","") + "; " + note).strip("; ") if s.get("acquired_lineage") else note

        for sg in (P.get("news", {}) or {}).get("signals", []):
            add_signal(aid, sg.get("signal_type","program_news"), sg)
        for sg in (P.get("conference", {}) or {}).get("signals", []):
            add_signal(aid, "conference_talk", sg)
            for sp in sg.get("named_people", [])[:3]:
                add_person(aid, sp, persona="Conference speaker", why_champion=sg.get("topic",""),
                           surfaced_via="conference", source_url=sg.get("url",""),
                           site_id=site_for(aid, sg.get("matched_city","")))
        ingest_pubs(aid, (P.get("publications", {}) or {}).get("publications", []))
        for jp in jobs.get("postings", []):
            add_signal(aid, "job_posting", jp)

    # ---- publications for all accounts ----
    for blk in data.get("publications_by_account", []):
        baid = blk.get("account_id")
        if baid in acc_by_id:
            ingest_pubs(baid, blk.get("publications", []))
            if not acc_by_id[baid].get("last_refreshed"): acc_by_id[baid]["last_refreshed"] = TODAY

    write("accounts.csv", accounts, list(accounts[0].keys()))
    write("sites.csv", sites, list(sites[0].keys()))
    write("people.csv", people, PEOPLE_FIELDS)
    write("signals.csv", signals, SIG_FIELDS)

    print("=== merge complete ===")
    print("signals added by type:", dict(added))
    print(f"signals total: {len(signals)} | people total: {len(people)}")
    print(f"pilot AZ decision_power_confidence: {acc_by_id['astrazeneca'].get('decision_power_confidence','')}")

if __name__ == "__main__":
    main()

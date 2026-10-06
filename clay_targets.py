#!/usr/bin/env python3
"""
Build the Clay lookup list from the named site heads on KEEP sites.

sites.csv carries `named_site_head` as evidence prose ("Margaret Faul, VP Drug Substance Tech & MA
Site Head"; "senior leads: A - Title; B - Title"). This pulls the proper names out of that prose,
dedupes them per account, attaches the company domain Clay needs, and ranks accounts by pipeline
status so a credit-capped run spends on live deals first.

Writes data/clay_targets.csv. Read-only otherwise.  python3 clay_targets.py
"""
import csv, re
import aiq

DOMAIN = aiq.DOMAIN
PRIORITY = {"Active Pipeline":1,"Active Prospect":1,"Active Partnership":1,"Closed Won":1,
            "No Deal Found":2,"":2,"Closed Lost":3}

PART = r"(?:[A-Z][a-zA-Z'\-]+\.?|[A-Z]\.|de|van|von|der|da|di|del|le|la)"
NAME = re.compile(r"(?<![A-Za-z\-])([A-Z][a-zA-Z'\-]+(?:\s+" + PART + r"){1,3})\s*(?:-|–|—|\(|,)\s*([^;\)]{3,110})")
TITLE_KW = re.compile(r"\b(head|director|vp|vice president|president|svp|evp|lead|manager|chief|officer|"
                      r"scientist|principal|site|plant|general manager|managing director|executive|fellow|"
                      r"engineer|associate|specialist|leader|cmc|msat|quality|operations|manufacturing)\b", re.I)
STOP = set("""global site head heads director quality process development manufacturing biologics drug
substance center centre campus aesthetics allergan vector gene therapy cell research park triangle executive
president senior chief officer vice sr vp lead leads none no public named plant north south east west new san
francisco cambridge boston hq r&d cmc msat pd api sterile fill finish operations technical business unit
division group company inc ltd gmbh ag sa spa biotech pharma pharmaceuticals therapeutics sciences science
laboratory laboratories facility facilities device devices commercial clinical medical regulatory affairs
supply chain external partner partners partnership contract cdmo cmo third party small large molecule
molecules antibody antibodies adc adcs vaccine vaccines plasma protein proteins peptide peptides oligo
oligonucleotide rna mrna dna viral vectors gene-therapy upstream downstream bioprocess bioprocessing
engineering digital data it analytics automation mes lims eln historian osi pi""".split())

def names_in(text):
    out = []
    for m in NAME.finditer(text or ""):
        name, title = m.group(1).strip(), m.group(2).strip().rstrip(",;.- ")
        toks = [t.lower().strip(".") for t in name.split()]
        if any(t in STOP for t in toks):
            continue
        if not TITLE_KW.search(title):
            continue
        out.append((name, title[:100]))
    return out

def main():
    acc = {a["account_id"]: a for a in aiq.load("accounts.csv")}
    rows, seen = [], {}
    for s in aiq.load("sites.csv"):
        a = acc.get(s["account_id"])
        if not a or a["active"] != "true" or a["in_universe"] != "true":
            continue
        if (s["verdict_manual"] or s["verdict_auto"]) != "KEEP":
            continue
        for name, title in names_in(s["named_site_head"]):
            key = (s["account_id"], re.sub(r"[^a-z]", "", name.lower()))
            if key in seen:
                seen[key]["site_ids"] += "|" + s["site_id"]
                continue
            r = {"target_id": "%s--ct-%s" % (s["account_id"], re.sub(r"[^a-z]", "", name.lower())[:24]),
                 "account_id": s["account_id"], "company": a["company"], "domain": DOMAIN[s["account_id"]],
                 "priority": str(PRIORITY.get(a["pipeline_status"], 2)), "pipeline_status": a["pipeline_status"],
                 "site_ids": s["site_id"], "site": s["site"], "name": name, "title_hint": title,
                 "source_text": (s["named_site_head"] or "")[:300]}
            seen[key] = r
            rows.append(r)
    rows.sort(key=lambda r: (r["priority"], r["account_id"], r["site_ids"]))
    fields = ["target_id","account_id","company","domain","priority","pipeline_status","site_ids","site",
              "name","title_hint","source_text"]
    with open(aiq.HERE + "/data/clay_targets.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    by_p = {}
    for r in rows: by_p[r["priority"]] = by_p.get(r["priority"], 0) + 1
    print("targets: %d distinct people across %d accounts; by priority %s" %
          (len(rows), len({r["account_id"] for r in rows}), by_p))
    for r in rows:
        print("P%s %-22s %-28s | %s" % (r["priority"], r["account_id"], r["name"][:28], r["title_hint"][:60]))

if __name__ == "__main__":
    main()

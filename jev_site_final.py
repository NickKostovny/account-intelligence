#!/usr/bin/env python3
"""One verdict per site for the briefs, with the basis for it.

Order of trust (from the Sep 30 trial: when the policy map and Jev agree they were right 21/21;
when they disagree one of them was right every time, so disagreements go to the source check):
  1. checked against a public source (data/jev/*_final.csv from the source-check runs)
  2. two readings agree (policy map verdict == Jev verdict in data/jev/site_fit.csv)
  3. otherwise the site is 'review' (needs the source check or a person)

  python3 jev_site_final.py      # -> data/jev/site_verdict_final.csv (no network)
"""
import csv, glob, os, re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
JEV = os.path.join(HERE, "data", "jev")
OUT_FAMILY = {"CLOSED", "GAP", "DROP"}
NOT_ANIMAL_ONLY = re.compile(r"small|mixed|r&d|research|diagnos|unclear|no pd|no mfg|office|support", re.I)


def same(a, b):
    return a == b or {a, b} <= OUT_FAMILY


def rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []


def main():
    fit = rows(os.path.join(JEV, "site_fit.csv"))
    checked = {}
    for path in sorted(glob.glob(os.path.join(JEV, "*_final.csv"))):   # later runs win
        if os.path.basename(path) == "site_verdict_final.csv":
            continue
        for r in rows(path):
            checked[r["site_id"]] = r
    out = []
    for f in fit:
        sid, pol, jev = f["site_id"], f.get("policy_verdict", ""), f["jev_verdict"]
        c = checked.get(sid)
        if c and c["final_verdict"] != "NEEDS_PERSON":
            v, reason = c["final_verdict"], c["reason"]
            # the checks ran under the old animal-health FLAG; Nick 2026-09-30 keeps animal health, so a
            # FLAG whose only reason is animal health becomes KEEP (small-mol, mixed or R&D-only still FLAG)
            if v == "FLAG" and "animal" in reason.lower() and not NOT_ANIMAL_ONLY.search(reason):
                v, reason = "KEEP", reason + " (animal health is KEEP)"
            rec = {"verdict": v, "reason": reason, "basis": "checked",
                   "source_url": c.get("source_url", ""), "source_date": c.get("source_date", "")}
        elif c:
            rec = {"verdict": "REVIEW", "reason": "sources conflict", "basis": "needs person",
                   "source_url": c.get("source_url", ""), "source_date": c.get("source_date", "")}
        elif jev != "REVIEW" and same(pol, jev):
            rec = {"verdict": pol if pol else jev, "reason": f.get("reason_tag", ""), "basis": "readings agree",
                   "source_url": "", "source_date": ""}
        else:
            rec = {"verdict": "REVIEW", "reason": f.get("unsure_on", "") or "readings disagree", "basis": "not checked",
                   "source_url": "", "source_date": ""}
        out.append({"site_id": sid, "account_id": f["account_id"], **rec, "policy_verdict": pol, "jev_verdict": jev})
    cols = ["site_id", "account_id", "verdict", "reason", "basis", "source_url", "source_date", "policy_verdict", "jev_verdict"]
    with open(os.path.join(JEV, "site_verdict_final.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    print("basis:", dict(Counter(r["basis"] for r in out)))
    print("verdict:", dict(Counter(r["verdict"] for r in out)))


if __name__ == "__main__":
    main()

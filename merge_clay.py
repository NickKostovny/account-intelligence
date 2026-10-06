#!/usr/bin/env python3
"""
Fold Clay people-lookup results into data/site_heads.csv.

Input:  data/cache/clay/batch*.json  (written by the lookup agents; one object per batch with a
        `results` list -- see clay_targets.py for how the names were chosen)
Join:   data/clay_targets.csv gives target_id -> account_id + the site_ids that named this person.
Output: data/site_heads.csv, upserted by site_head_id (= target_id). Rows that Clay could not find
        are kept with found=false so the brief can say "not matched on LinkedIn" instead of nothing.

  python3 merge_clay.py            # merge every batch file
  python3 merge_clay.py --report   # coverage by account, no writes
"""
import argparse, csv, glob, html, json, os, re, time
import aiq

FIELDS = ["site_head_id", "account_id", "site_ids", "name", "title_hint", "found", "match",
          "clay_name", "clay_title", "clay_company", "linkedin_url", "location", "email",
          "email_status", "entity_id", "task_id", "checked_at", "source"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    targets = {r["target_id"]: r for r in aiq.load("clay_targets.csv")}
    existing = {r["site_head_id"]: r for r in aiq.load("site_heads.csv")} if \
        os.path.exists(os.path.join(aiq.DATA, "site_heads.csv")) else {}
    if not a.report:
        n_files = 0
        for f in sorted(glob.glob(os.path.join(aiq.DATA, "cache", "clay", "batch*.json"))):
            b = json.load(open(f))
            n_files += 1
            for r in b.get("results", []):
                t = targets.get(r.get("target_id"))
                if not t:
                    continue
                row = existing.get(t["target_id"], {"site_head_id": t["target_id"], "source": "clay_mcp",
                                                     "email": "", "email_status": ""})
                row.update({
                    "account_id": t["account_id"], "site_ids": t["site_ids"], "name": t["name"],
                    "title_hint": t["title_hint"],
                    "found": "true" if r.get("found") else "false",
                    "match": r.get("status", ""), "clay_name": html.unescape(r.get("clay_name", "") or ""),
                    "clay_title": html.unescape(r.get("clay_title", "") or ""),
                    "clay_company": html.unescape(r.get("clay_company", "") or ""),
                    "linkedin_url": r.get("linkedin_url", ""), "location": r.get("location", ""),
                    "entity_id": r.get("entity_id", ""), "task_id": b.get("task_id", ""),
                    "checked_at": (b.get("ran_at") or aiq.today())[:10],
                })
                if r.get("email"):
                    em = r["email"].strip()
                    local = em.split("@")[0]
                    st = r.get("email_status", "found")
                    # "m.c@gsk.com" is a pattern guess, not a mailbox anyone confirmed.
                    if re.fullmatch(r"[a-z](\.[a-z])?", local, re.I) or len(local) <= 2:
                        st = "suspect"
                    # An address on an acquired company's old domain (shire.com for a Takeda head,
                    # trilliumtherapeutics.com at Pfizer) is probably dead. Keep it, do not render it.
                    if st == "found" and "@" in em:
                        root = lambda d: d.lower().split(".")[-2] if d.count(".") >= 1 else d.lower()
                        er = root(em.split("@")[1])
                        allowed = [aiq.DOMAIN.get(t["account_id"], "")] + aiq.EMAIL_DOMAINS.get(t["account_id"], [])
                        if not any(er and root(d) and (er in root(d) or root(d) in er) for d in allowed if d):
                            st = "suspect_domain"
                    row["email"], row["email_status"] = em, st
                elif r.get("email_status"):
                    row["email"], row["email_status"] = "", r["email_status"]
                existing[t["target_id"]] = row
        rows = sorted(existing.values(), key=lambda r: (r["account_id"], r["name"]))
        aiq.write("site_heads.csv", rows, FIELDS)
        print("merged %d batch file(s) -> site_heads.csv: %d rows" % (n_files, len(rows)))
    by = {}
    for r in existing.values():
        d = by.setdefault(r["account_id"], [0, 0, 0])
        d[0] += 1
        d[1] += r["found"] == "true"
        d[2] += bool(r.get("email"))
    tot = [sum(v[i] for v in by.values()) for i in range(3)]
    print("site heads: %d checked, %d found on LinkedIn, %d with email" % tuple(tot))
    for k in sorted(by):
        print("  %-22s checked %2d  found %2d  email %2d" % (k, *by[k]))

if __name__ == "__main__":
    main()

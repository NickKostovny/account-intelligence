#!/usr/bin/env python3
"""
Fold Clay ROLE searches (site-scoped "who leads this site" queries) into data/site_heads.csv.

Different input from merge_clay.py: there the input was a known name; here Clay was asked for people
at the company whose headline reads like site leadership AND whose location is the site's city. The
agent that ran the query marks which candidates it chose (`chosen: true`) and why (`why`).

Input:  data/cache/clay/roles/<site_id>.json
        {"site_id","account_id","query","task_id","ran_at","credit_notes",
         "candidates":[{"name","title","linkedin_url","location","entity_id","chosen":bool,"why",
                        "email","email_status"}]}
Output: rows in data/site_heads.csv with site_head_id = "<site_id>--rh-<nameslug>", match = "role_search",
        source = "clay_role_search". Unchosen candidates are NOT written (they were not the site head).

  python3 merge_roles.py
"""
import glob, html, json, os, re
import aiq
from merge_clay import FIELDS

def main():
    existing = {r["site_head_id"]: r for r in aiq.load("site_heads.csv")} if \
        os.path.exists(os.path.join(aiq.DATA, "site_heads.csv")) else {}
    n_files = n_rows = 0
    for f in sorted(glob.glob(os.path.join(aiq.DATA, "cache", "clay", "roles", "*.json"))):
        b = json.load(open(f))
        n_files += 1
        for c in b.get("candidates", []):
            if not c.get("chosen"):
                continue
            slug = re.sub(r"[^a-z]", "", (c.get("name") or "").lower())[:24]
            hid = "%s--rh-%s" % (b["site_id"], slug)
            row = existing.get(hid, {"site_head_id": hid, "email": "", "email_status": ""})
            em = (c.get("email") or "").strip()
            st = c.get("email_status", "") or ("found" if em else "")
            if em:
                local = em.split("@")[0]
                if re.fullmatch(r"[a-z](\.[a-z])?", local, re.I) or len(local) <= 2:
                    st = "suspect"
                root = lambda d: d.lower().split(".")[-2] if d.count(".") >= 1 else d.lower()
                er = root(em.split("@")[1])
                allowed = [aiq.DOMAIN.get(b["account_id"], "")] + aiq.EMAIL_DOMAINS.get(b["account_id"], [])
                if st == "found" and not any(er and root(d) and (er in root(d) or root(d) in er) for d in allowed if d):
                    st = "suspect_domain"
            row.update({
                "account_id": b["account_id"], "site_ids": b["site_id"],
                "name": html.unescape(c.get("name") or ""), "title_hint": b.get("query", "")[:100],
                "found": "true", "match": "role_search",
                "clay_name": html.unescape(c.get("name") or ""),
                "clay_title": html.unescape(c.get("title") or ""),
                "clay_company": html.unescape(c.get("company") or ""),
                "linkedin_url": c.get("linkedin_url", ""), "location": c.get("location", ""),
                "email": em, "email_status": st,
                "entity_id": c.get("entity_id", ""), "task_id": b.get("task_id", ""),
                "checked_at": (b.get("ran_at") or aiq.today())[:10], "source": "clay_role_search",
            })
            existing[hid] = row
            n_rows += 1
    rows = sorted(existing.values(), key=lambda r: (r["account_id"], r["name"]))
    aiq.write("site_heads.csv", rows, FIELDS)
    print("merged %d role file(s), %d chosen people -> site_heads.csv: %d rows" % (n_files, n_rows, len(rows)))

if __name__ == "__main__":
    main()

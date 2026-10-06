#!/usr/bin/env python3
"""Label collected news items with Jev and roll them up per site and per account.

Input: data/news/news_items.csv from news_collect.py (headline, source, date, snippet).
Jev answers, per item: is it really about this company, is it about operations, which trigger
it reports (capex, opening, closure, site sale, approval, manufacturing deal, leadership), and
which of the account's sites it concerns (a Choice over site ids from sites.csv, or company-wide).
Code owns dates, counts and the rollups; a label shows only when decisive (Noul >= 0.8,
Choice confidence >= 0.8). Headlines are public text; no people data is sent.

  python3 jev_news.py          # needs network (Jev)
Outputs: data/news/news_labels.csv, data/news/site_news.csv, data/news/account_news.csv
"""
import csv, json, os, re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from jev_client import ask, usage_summary

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS = os.path.join(HERE, "data", "news")
YES = 0.8

TRIGGERS = [   # priority order: the first decisive one is the item's trigger
    ("site_deal", "Does it say a site or plant was sold, bought or transferred between companies?"),
    ("closure", "Does it say a site closes or shuts down, or that the company cuts jobs?"),
    ("opening", "Does it say a site or plant opened or started operating?"),
    ("capex", "Does it announce an investment, an expansion, a new building or added capacity?"),
    ("manufacturing_deal", "Does it announce a manufacturing, supply or contract manufacturing agreement?"),
    ("approval", "Does it report a regulatory approval of a product?"),
    ("leadership", "Does it report a new leader or a leadership change?"),
]
LABEL = {"site_deal": "site deal", "closure": "closure", "opening": "opening", "capex": "capex",
         "manufacturing_deal": "mfg deal", "approval": "approval", "leadership": "leadership"}


def rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []


def questions(company, sites):
    q = {
        "about_company": {"type": "noul", "instructions": f"Is `item` news about {company} itself, not another company with a similar name and not a list of many stocks?"},
        "ops": {"type": "noul", "instructions": f"Is `item` about {company}'s sites, manufacturing, research, products or organisation, and not about share price, analyst ratings, lawsuits or marketing?"},
    }
    for k, text in TRIGGERS:
        q[k] = {"type": "noul", "instructions": "About `item`: " + text}
    if sites:
        crit = {sid: f"{name}, {city}" for sid, name, city in sites[:254]}
        crit["company_wide"] = "No single site, or a site that is not in this list."
        q["site"] = {"type": "choice", "instructions": f"Which of {company}'s sites is `item` about?", "criteria": crit}
    return q


WORD = re.compile(r"[a-z0-9$]+")
STOPWORDS = set("the a an of to in for and on at with as by from its it is new us".split())


def words(t):
    return {w for w in WORD.findall(t.lower()) if w not in STOPWORDS and len(w) > 2}


def dedupe(items):
    """One story told by several outlets counts once: same account, same trigger, published within
    about a month, and headlines sharing a quarter of their words. The first (newest) copy stays."""
    kept = []
    for r in items:
        w = words(r["title"])
        dup = any(k["account_id"] == r["account_id"] and k["trigger"] == r["trigger"]
                  and k["published"][:7] in (r["published"][:7], ) and w and
                  len(w & words(k["title"])) / len(w | words(k["title"])) >= 0.25 for k in kept[-60:])
        if not dup:
            kept.append(r)
    return kept


def main():
    items = rows(os.path.join(NEWS, "news_items.csv"))
    if not items:
        raise SystemExit("no data/news/news_items.csv yet: run news_collect.py first")
    comp = {r["account_id"]: r.get("company", r["account_id"]) for r in rows(os.path.join(HERE, "data", "accounts.csv"))}
    by_acct = defaultdict(list)
    for s in rows(os.path.join(HERE, "data", "sites.csv")):
        by_acct[s["account_id"]].append((s["site_id"], re.sub(r"\s*\(.*?\)", "", s["site"]).strip() or s["site"], s["city"]))
    print("Jev before:", usage_summary())

    def one(it):
        company = comp.get(it["account_id"], it["account_id"])
        state = {"item": {"title": it.get("title", ""), "source": it.get("source", ""), "snippet": (it.get("snippet") or "")[:600]}}
        a = ask(state, questions(company, by_acct.get(it["account_id"], [])), tag="news")["answers"]
        trig = next((k for k, _ in TRIGGERS if a[k]["noul"] >= YES), "")
        keep = a["about_company"]["noul"] >= YES and a["ops"]["noul"] >= YES and bool(trig)
        site = ""
        if "site" in a and a["site"]["confidence"] >= YES and a["site"]["choice"] != "company_wide":
            site = a["site"]["choice"]
        rec = {k: it.get(k, "") for k in ("item_id", "account_id", "query_kind", "site_id", "title", "source", "url", "published")}
        rec.update({"about_company_p": round(a["about_company"]["noul"], 3), "ops_p": round(a["ops"]["noul"], 3),
                    "trigger": LABEL.get(trig, ""), "keep": keep, "jev_site": site,
                    "site_conf": round(a["site"]["confidence"], 3) if "site" in a else "",
                    **{k + "_p": round(a[k]["noul"], 3) for k, _ in TRIGGERS}, "model": "jev-1.13.0"})
        return rec

    with ThreadPoolExecutor(6) as ex:
        recs = list(ex.map(one, items))
    fields = list(recs[0].keys())
    with open(os.path.join(NEWS, "news_labels.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(recs)

    kept = dedupe(sorted([r for r in recs if r["keep"]], key=lambda r: r["published"], reverse=True))
    site_rows, acct_rows = [], []
    per_site = defaultdict(list)
    for r in kept:
        if r["jev_site"]:
            per_site[r["jev_site"]].append(r)
    for sid, rs in per_site.items():
        t = Counter(r["trigger"] for r in rs)
        site_rows.append({"site_id": sid, "account_id": rs[0]["account_id"], "n_items": len(rs),
                          "latest_date": rs[0]["published"], "latest_trigger": rs[0]["trigger"],
                          "latest_title": rs[0]["title"], "latest_url": rs[0]["url"], "latest_source": rs[0]["source"],
                          "triggers": "|".join(f"{k}:{v}" for k, v in t.most_common())})
    per_acct = defaultdict(list)
    for r in kept:
        per_acct[r["account_id"]].append(r)
    for aid, rs in per_acct.items():
        acct_rows.append({"account_id": aid, "n_items": len(rs),
                          "top": json.dumps([{k: r[k] for k in ("published", "trigger", "title", "source", "url", "jev_site")} for r in rs[:6]], ensure_ascii=False)})
    for name, data, cols in (("site_news.csv", site_rows, ["site_id", "account_id", "n_items", "latest_date", "latest_trigger", "latest_title", "latest_url", "latest_source", "triggers"]),
                             ("account_news.csv", acct_rows, ["account_id", "n_items", "top"])):
        with open(os.path.join(NEWS, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(data)
    print(f"items {len(recs)} | kept {len(kept)} | sites with news {len(site_rows)} | accounts with news {len(acct_rows)}")
    print("triggers:", dict(Counter(r["trigger"] for r in kept)))
    print("Jev after:", usage_summary())


if __name__ == "__main__":
    main()

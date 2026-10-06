#!/usr/bin/env python3
"""Check site verdicts against INDEPENDENT public sources, with no person in the loop.

The site verdicts in data/jev/site_fit.csv come from Jev reading Claude's site-map text, so a
mistake in that text passes straight through. This script takes evidence that agents found on
the web with only company + site + city to go on (data/jev/mvp_evidence.json), then:

  1. re-fetches every source page and keeps only excerpts that really appear on it (verbatim
     contract: a passage the page does not contain is dropped, never "fixed");
  2. asks Jev whether the text is about THIS site, plus the same SITE_QS the verdict uses;
  3. runs pilot_jev.site_verdict() on those answers and compares with the summary verdict.

Outputs data/jev/mvp_verification.csv and data/jev/mvp_to_adjudicate.json (contradictions and
unsure cases for a reasoning model). Nothing in data/*.csv is touched.

  python3 jev_verify_sources.py                    # trial: mvp_evidence.json -> mvp_*
  python3 jev_verify_sources.py <evidence.json> <prefix>   # e.g. check206_evidence.json check206
"""
import csv, html, json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

from jev_client import ask, usage_summary
from pilot_jev import SITE_QS, site_verdict, rows

HERE = os.path.dirname(os.path.abspath(__file__))
JEV = os.path.join(HERE, "data", "jev")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"

ABOUT_Q = {"type": "noul",
           "instructions": "Do `source_titles` and `source_text` describe this company's site at `site` in `city` (or the same facility under a former or new owner), and not a different site or the company in general?",
           "criteria": {"true": "The text is about this specific site or facility in this city.",
                        "false": "The text is about another site, another company, or only the company as a whole."}}


def page_text(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception as e:
        return None, type(e).__name__
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    return norm(html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))), "ok"


def norm(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def check_sources(site):
    """Excerpts that appear on the re-fetched page, plus a fetch status per source."""
    kept, status = [], []
    for src in site.get("sources", []):
        text, why = page_text(src["url"])
        ok = [x for x in src.get("excerpts", []) if text and norm(x)[:400] in text]
        status.append({"url": src["url"], "fetch": why, "excerpts": len(src.get("excerpts", [])), "verified": len(ok),
                       "kind": src.get("publisher_kind", ""), "date": src.get("date", ""), "title": src.get("title", "")})
        kept += [(src, x) for x in ok]
    return kept, status


def main(evidence="mvp_evidence.json", prefix="mvp"):
    ev = json.load(open(os.path.join(JEV, evidence), encoding="utf-8"))
    fit = {r["site_id"]: r for r in rows_jev("site_fit.csv")}
    sites = {s["site_id"]: s for s in rows("sites.csv")}
    print("Jev before:", usage_summary())

    def one(e):
        sid = e["site_id"]
        s = sites.get(sid, {})
        kept, status = check_sources(e) if e.get("found") else ([], [])
        rec = {"site_id": sid, "account_id": s.get("account_id", ""), "site": s.get("site", ""), "city": s.get("city", ""),
               "summary_verdict": fit.get(sid, {}).get("jev_verdict", ""), "summary_tag": fit.get(sid, {}).get("reason_tag", ""),
               "claude_verdict": fit.get(sid, {}).get("claude_verdict", ""), "found": e.get("found", False),
               "sources": len(status), "sources_fetched": sum(x["fetch"] == "ok" for x in status),
               "excerpts_verified": len(kept), "agent_note": e.get("note", ""),
               "best_url": (kept[0][0]["url"] if kept else (status[0]["url"] if status else "")),
               "best_kind": (kept[0][0].get("publisher_kind", "") if kept else ""),
               "best_date": (kept[0][0].get("date", "") if kept else "")}
        if not kept:
            rec.update({"about_site_p": "", "evidence_verdict": "", "evidence_tag": "", "evidence_unsure": "",
                        "outcome": "no verified evidence"})
            return rec, e, status, kept
        titles = sorted({src.get("title", "") for src, _ in kept if src.get("title")})
        state = {"company": s.get("account_id", ""), "site": s.get("site", ""), "city": s.get("city", ""),
                 "source_titles": titles, "source_text": " ".join(x for _, x in kept)[:12000]}
        a = ask(state, {**SITE_QS, "about_site": ABOUT_Q}, tag="verify_sources")["answers"]
        about = a["about_site"]["noul"]
        v, tag, unsure = site_verdict(a)
        support, conflict = claims(a, fit.get(sid, {}))
        rec.update({"about_site_p": round(about, 3), "evidence_verdict": v, "evidence_tag": tag, "evidence_unsure": unsure,
                    "supports": "|".join(support), "conflicts": "|".join(conflict)})
        if about <= 0.2:
            rec["outcome"] = "source not about this site"
        elif conflict:
            rec["outcome"] = "contradicted"
        elif support:
            rec["outcome"] = "confirmed"
        else:
            rec["outcome"] = "source too thin"
        return rec, e, status, kept

    with ThreadPoolExecutor(6) as ex:
        out = list(ex.map(one, ev))
    recs = [r for r, _, _, _ in out]
    with open(os.path.join(JEV, prefix + "_verification.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)
    todo = [{"site_id": r["site_id"], "outcome": r["outcome"], "summary_verdict": r["summary_verdict"],
             "summary_tag": r["summary_tag"], "evidence_verdict": r["evidence_verdict"], "evidence_tag": r["evidence_tag"],
             "summary_text": {"functions": sites.get(r["site_id"], {}).get("functions", ""),
                              "modality": sites.get(r["site_id"], {}).get("modality", "")},
             "sources": st, "verified_excerpts": [{"url": s["url"], "text": x} for s, x in kept], "agent_note": e.get("note", "")}
            for r, e, st, kept in out if r["outcome"] in ("contradicted", "source too thin", "source not about this site",
                                                          "no verified evidence", "evidence decides")]
    json.dump(todo, open(os.path.join(JEV, prefix + "_to_adjudicate.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    from collections import Counter
    print("outcomes:", dict(Counter(r["outcome"] for r in recs)))
    print("sources fetched:", sum(r["sources_fetched"] for r in recs), "/", sum(r["sources"] for r in recs),
          "| sites with a verified excerpt:", sum(1 for r in recs if r["excerpts_verified"]), "/", len(recs))
    print("to adjudicate:", len(todo), "| Jev after:", usage_summary())


YES, NO = 0.8, 0.2
CORE = ("drug_substance", "pd_msat", "drug_product", "manufacturing")


def claims(a, f):
    """(supported, contradicted) conditions. A short quote that does not mention PD is not evidence
    that a site has no PD, so a Noul 'no' from the source is never a contradiction; only a source
    'yes', a closure/sale, or a named modality can contradict the summary."""
    sup, con = [], []
    ev_closed = sum(a["status"]["probabilities"].get(o, 0) for o in ("closed", "sold_or_divested", "closure_announced"))
    ev_planned = a["status"]["probabilities"].get("planned_not_running", 0)
    su_closed = float(f.get("status_p_closed") or 0)
    su_planned = float(f.get("status_p_planned") or 0)
    if ev_closed >= YES:
        (sup if su_closed >= 0.5 else con).append("closed/sold")
    elif ev_planned >= YES:
        (sup if su_planned >= 0.5 else con).append("planned")
    elif a["status"]["probabilities"].get("operating", 0) >= YES and su_closed >= YES:
        con.append("operating")
    for q in CORE + ("support", "research", "cdmo"):
        if a[q]["noul"] >= YES:
            (sup if f.get(q) == "yes" else con if f.get(q) == "no" else sup).append(q)
    mod = a["modality"]
    if mod["confidence"] >= YES and mod["choice"] != "not_stated":
        if mod["choice"] == f.get("modality"):
            sup.append("modality:" + mod["choice"])
        elif f.get("modality") in ("biologic_only", "small_molecule_only", "mixed"):
            # 'mixed' vs one side is a partial view from a short quote, not a conflict
            (sup if "mixed" in (mod["choice"], f.get("modality")) else con).append("modality:" + mod["choice"])
    return sup, con


def rows_jev(name):
    return list(csv.DictReader(open(os.path.join(JEV, name), encoding="utf-8")))


if __name__ == "__main__":
    main(*sys.argv[1:3])

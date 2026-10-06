#!/usr/bin/env python3
"""
Census the applicant-tracking system behind all 49 target accounts.

The point: what we can know about an account is decided by its ATS, not by our effort.
  * Server-rendered careers sites (Phenom and friends) are crawled by Wayback -> ~2 years of history.
  * Workday, Greenhouse, Lever, SmartRecruiters and the rest render requisitions in JS, so the archive
    has nothing -- but most expose a clean public JSON API with FULL descriptions for what is open
    today, which is better than the archive for the present and useless for the past.

So each account falls into one of three buckets, and the bucket sets the ceiling:
  history_available   archive-crawlable -> depth backwards, ~50% body yield
  api_current_only    JSON API -> 100% body yield, no history, accretes from first run
  needs_work          neither found yet

Read-only. Writes data/ats_census.csv.

  python3 ats_census.py            # probe every active account
  python3 ats_census.py --report   # print the census, no network
"""
import argparse, json, os, re, time
import urllib.error, urllib.request

import aiq

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
FIELDS = ["account_id", "company", "careers_url", "ats", "ats_host", "bucket", "api_shape",
          "jsonld_jobposting", "http", "notes", "probed_at"]

# Ordered most-specific first. Each entry: (ats name, regex over page HTML, bucket, api shape).
# `bucket` is the ceiling on what this account can ever give us.
ATS = [
    ("workday", r"([a-z0-9\-]+)\.(wd\d)\.myworkdayjobs\.com", "api_current_only",
     "POST /wday/cxs/<tenant>/<site>/jobs  + GET .../job/<path> for the description"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/([a-z0-9\-]+)", "api_current_only",
     "GET boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true  (full HTML descriptions)"),
    ("lever", r"jobs\.lever\.co/([a-z0-9\-]+)", "api_current_only",
     "GET api.lever.co/v0/postings/<co>?mode=json"),
    ("smartrecruiters", r"jobs\.smartrecruiters\.com/([A-Za-z0-9\-]+)", "api_current_only",
     "GET api.smartrecruiters.com/v1/companies/<id>/postings"),
    ("ashby", r"jobs\.ashbyhq\.com/([a-z0-9\-]+)", "api_current_only",
     "POST api.ashbyhq.com/posting-api/job-board/<co>"),
    ("successfactors", r"(career\d*\.successfactors\.(?:eu|com))", "api_current_only",
     "RSS/JSON on the career site; per-tenant"),
    ("icims", r"([a-z0-9\-]+)\.icims\.com", "history_available",
     "server-rendered; archive-crawlable"),
    ("avature", r"([a-z0-9\-]+)\.avature\.net", "history_available",
     "server-rendered; archive-crawlable"),
    ("taleo", r"([a-z0-9\-]+)\.taleo\.net", "history_available",
     "server-rendered; archive-crawlable"),
    ("eightfold", r"([a-z0-9\-]+)\.eightfold\.ai", "api_current_only",
     "GET /api/apply/v2/jobs"),
    ("phenom", r"(?:phenompeople|phenom\.com|/job/[^/\"']+/[^/\"']+/\d+/\d+)", "history_available",
     "server-rendered /job/<city>/<slug>/<board>/<reqid>; archive-crawlable"),
]

CANDIDATES = ["https://careers.{d}/", "https://www.{d}/careers", "https://www.{d}/careers.html",
              "https://jobs.{d}/", "https://{d}/careers", "https://www.{d}/en/careers",
              "https://www.{d}/careers/", "https://www.{d}/en_us/careers.html"]

# Company -> web domain. Guessing from the company name fails often enough to matter (emdgroup for
# Merck KGaA, gene.com for Genentech, modernatx for Moderna), and a wrong domain reads as
# "needs_work" when the real answer was one lookup away.
DOMAIN = {
    "astrazeneca": "astrazeneca.com", "biogen": "biogen.com", "abbvie": "abbvie.com",
    "amgen": "amgen.com", "pfizer": "pfizer.com", "gsk": "gsk.com", "sanofi": "sanofi.com",
    "novartis": "novartis.com", "roche": "roche.com", "genentech": "gene.com",
    "merck-and-co": "merck.com", "merck-kgaa": "emdgroup.com", "eli-lilly": "lilly.com",
    "bristol-myers-squibb": "bms.com", "johnson-johnson": "jnj.com", "takeda": "takeda.com",
    "moderna": "modernatx.com", "regeneron": "regeneron.com", "vertex": "vrtx.com",
    "gilead-sciences": "gilead.com", "novo-nordisk": "novonordisk.com", "lonza": "lonza.com",
    "csl": "csl.com", "bayer": "bayer.com", "boehringer-ingelheim": "boehringer-ingelheim.com",
    "biontech": "biontech.com", "alnylam": "alnylam.com", "biomarin": "biomarin.com",
    "incyte": "incyte.com", "jazz-pharmaceuticals": "jazzpharma.com", "ucb": "ucb.com",
    "ipsen": "ipsen.com", "lundbeck": "lundbeck.com", "genmab": "genmab.com",
    "ascendis-pharma": "ascendispharma.com", "beigene": "beigene.com",
    "daiichi-sankyo": "daiichisankyo.com", "astellas": "astellas.com", "eisai": "eisai.com",
    "otsuka": "otsuka.co.jp", "chugai": "chugai-pharm.co.jp", "fujifilm": "fujifilmdiosynth.com",
    "samsung-biologics": "samsungbiologics.com", "agc-biologics": "agcbio.com",
    "recipharm": "recipharm.com", "zoetis": "zoetis.com", "novavax": "novavax.com",
    "alexion": "alexion.com", "seagen": "seagen.com",
}


def get(url, timeout=20):
    r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return f.status, f.read(500000).decode("utf-8", "replace")


def detect(html):
    for name, pat, bucket, shape in ATS:
        m = re.search(pat, html, re.I)
        if m:
            host = ""
            try:
                host = m.group(0)
            except IndexError:
                pass
            return name, host, bucket, shape
    return "", "", "", ""


def jsonld(html):
    """
    Does the page carry schema.org JobPosting markup? If it does, datePosted / jobLocation /
    hiringOrganization / description arrive already structured -- strictly better than parsing prose,
    and it is the one free upgrade that improves date accuracy, which is our weakest field.
    """
    for m in re.finditer(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html):
        blob = m.group(1)
        if '"JobPosting"' in blob or "'JobPosting'" in blob:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--account", action="append")
    a = ap.parse_args()

    existing = {r["account_id"]: r for r in aiq.load("ats_census.csv")}

    if a.report:
        from collections import Counter
        rows = list(existing.values())
        if not rows:
            print("no census yet -- run without --report")
            return
        print("ATS census: %d accounts probed" % len(rows))
        print("\nby bucket (this is the ceiling on what each account can give us):")
        for b, n in Counter(r["bucket"] or "needs_work" for r in rows).most_common():
            print("  %-18s %d" % (b, n))
        print("\nby ATS:")
        for t, n in Counter(r["ats"] or "(undetected)" for r in rows).most_common():
            print("  %-18s %d" % (t, n))
        print("\nschema.org JobPosting markup present: %d of %d"
              % (sum(1 for r in rows if r["jsonld_jobposting"] == "true"), len(rows)))
        for b in ("history_available", "api_current_only", "needs_work", ""):
            names = sorted(r["account_id"] for r in rows if (r["bucket"] or "needs_work") ==
                           (b or "needs_work"))
            if names and b:
                print("\n%s (%d): %s" % (b, len(names), ", ".join(names)))
        return

    accounts = [x for x in aiq.load("accounts.csv")
                if x["active"] == "true" and x["in_universe"] == "true"
                and (not a.account or x["account_id"] in a.account)]
    print("probing %d account(s)" % len(accounts))
    for acc in accounts:
        aid = acc["account_id"]
        dom = DOMAIN.get(aid) or (re.sub(r"[^a-z0-9]", "", aiq.afold(acc["company"]).lower()) + ".com")
        found = None
        for tpl in CANDIDATES:
            url = tpl.format(d=dom)
            try:
                code, html = get(url)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                continue
            if len(html) < 3000:
                continue
            ats, host, bucket, shape = detect(html)
            if ats:
                found = {"account_id": aid, "company": acc["company"], "careers_url": url,
                         "ats": ats, "ats_host": host, "bucket": bucket, "api_shape": shape,
                         "jsonld_jobposting": "true" if jsonld(html) else "false",
                         "http": str(code), "notes": "", "probed_at": aiq.today()}
                break
            if found is None:
                found = {"account_id": aid, "company": acc["company"], "careers_url": url,
                         "ats": "", "ats_host": "", "bucket": "needs_work", "api_shape": "",
                         "jsonld_jobposting": "true" if jsonld(html) else "false",
                         "http": str(code), "notes": "careers page reachable, no ATS marker found",
                         "probed_at": aiq.today()}
        if found is None:
            found = {"account_id": aid, "company": acc["company"], "careers_url": "", "ats": "",
                     "ats_host": "", "bucket": "needs_work", "api_shape": "",
                     "jsonld_jobposting": "false", "http": "",
                     "notes": "no careers page reachable at guessed domains", "probed_at": aiq.today()}
        existing[aid] = found
        print("  %-24s %-16s %-18s %s" % (aid, found["ats"] or "-", found["bucket"],
                                          "json-ld" if found["jsonld_jobposting"] == "true" else ""))
        time.sleep(0.3)

    order = [x["account_id"] for x in accounts]
    rows = [existing[k] for k in order if k in existing] + \
           [v for k, v in existing.items() if k not in order]
    aiq.write("ats_census.csv", rows, FIELDS)
    print("\nwrote data/ats_census.csv (%d rows)" % len(rows))


if __name__ == "__main__":
    main()

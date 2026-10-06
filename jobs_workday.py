#!/usr/bin/env python3
"""
Tier-1 collector B: Workday tenants, via the CXS JSON API.

Why this exists. The Wayback collector (jobs_cdx.py) works on Phenom-style careers sites, where
requisition URLs are real server-rendered paths that the archive crawls -- AstraZeneca yielded 6,677.
It returns NOTHING for Workday, because Workday requisition pages are JS-rendered and were never
archived. Probing Biogen's tenant across four URL globs returned zero, and eight guessed hostnames
returned zero, so on the archive route Biogen -- one of the two named target accounts -- is simply
not available.

Workday does expose a clean JSON search API, and it is better than the archive for the present
snapshot: real titles, a parseable location string, a genuine posting recency, and a stable
requisition id. What it cannot give is history. That is fine, and it is the design the corpus was
built for: first_seen/last_seen accrete, so running this on a cadence GENERATES the time series
going forward rather than trying to buy the past.

  python3 jobs_workday.py --discover biogen        # find tenant + site from the careers page
  python3 jobs_workday.py --account biogen         # page the API into postings.csv
  python3 jobs_workday.py --all                    # every account with a workday host on file

Tenant names cannot be guessed: Biogen's is "biibhr", not "biogen". --discover reads the real host
out of the company careers page instead.
"""
import argparse, json, os, re, time
import urllib.error, urllib.request

import aiq
from jobs_cdx import FIELDS, titleise, rollup, _rescore

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
PAGE = 20
SLEEP = 0.4

CAREERS_GUESS = ["https://www.{d}/careers.html", "https://www.{d}/careers",
                 "https://{d}/careers", "https://www.{d}/en_us/careers.html"]


def get(url, timeout=30):
    r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return f.read().decode("utf-8", "replace")


def post(url, body, timeout=30):
    r = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                               headers={"User-Agent": UA, "Content-Type": "application/json",
                                        "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read())


def discover(company, domain_hint=""):
    """-> (host, tenant, wd_n, site) or (None,)*4. Reads the ATS host out of the careers page."""
    base = domain_hint or (re.sub(r"[^a-z0-9]", "", aiq.afold(company).lower()) + ".com")
    html = ""
    for tpl in CAREERS_GUESS:
        try:
            html = get(tpl.format(d=base))
            if len(html) > 4000:
                break
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            continue
    if not html:
        return None, None, None, None
    m = re.search(r"https://([a-z0-9\-]+)\.(wd\d)\.myworkdayjobs\.com/"
                  r"(?:([a-z]{2}-[A-Z]{2})/)?([A-Za-z0-9_\-]+)", html)
    if not m:
        return None, None, None, None
    tenant, wd, _loc, site = m.group(1), m.group(2), m.group(3), m.group(4)
    if site in ("wday", "introduceYourself"):
        site = "external"
    return "%s.%s.myworkdayjobs.com" % (tenant, wd), tenant, wd, site


def rel_date(posted_on):
    """
    'Posted 15 Days Ago' -> an ISO date 15 days back. '30+' is a floor, not a date, so it is left
    empty rather than pretended precise -- the corpus already distinguishes date kinds and a fake
    precision here would poison the only real posted dates we have.
    """
    s = (posted_on or "").lower()
    if "today" in s:
        return time.strftime("%Y-%m-%d"), "workday_posted_today"
    if "yesterday" in s:
        return time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)), "workday_posted_rel"
    m = re.search(r"(\d+)\+?\s*day", s)
    if m and "+" not in s:
        return time.strftime("%Y-%m-%d", time.localtime(time.time() - int(m.group(1)) * 86400)), \
               "workday_posted_rel"
    return "", ""


MULTI = re.compile(r"^\d+\s+Location", re.I)


def city_of(locations_text):
    """'Cambridge, MA' -> 'Cambridge, MA'. '2 Locations' names no city -- see detail_locations()."""
    t = (locations_text or "").strip()
    if not t or MULTI.match(t):
        return ""
    return t.split(";")[0].strip()


def detail_locations(host, site, path):
    """
    -> (primary_location, [additional...]).

    A requisition open at several sites comes back as "2 Locations" with no city in it, and simply
    dropping those lost 15 of Biogen's 204 -- including an Analyst, Statistical Programming and an
    Associate Director, AI/Data Science, both real bioprocess-adjacent roles. The job-detail endpoint
    carries the full list, so one extra GET per multi-location req recovers them.

    Attribution goes to the PRIMARY location, with the rest kept in `also_at`: a requisition open in
    two cities is one requisition, and counting it at both would inflate every site total.
    """
    api = "https://%s/wday/cxs/%s/%s%s" % (host, host.split(".")[0], site, path)
    try:
        r = urllib.request.Request(api, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(r, timeout=30) as f:
            info = (json.loads(f.read()).get("jobPostingInfo") or {})
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return "", []
    return (info.get("location") or ""), list(info.get("additionalLocations") or [])


def fetch_all(host, tenant, site, limit_pages=60):
    api = "https://%s/wday/cxs/%s/%s/jobs" % (host, tenant, site)
    out, offset, total = [], 0, None
    for _ in range(limit_pages):
        try:
            d = post(api, {"appliedFacets": {}, "limit": PAGE, "offset": offset, "searchText": ""})
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            print("      api error at offset %d: %s" % (offset, str(e)[:60]))
            break
        if total is None:
            total = d.get("total")
        posts = d.get("jobPostings") or []
        if not posts:
            break
        out += posts
        offset += PAGE
        if total and offset >= total:
            break
        time.sleep(SLEEP)
    return out, total


def ingest(account_ids=None, discover_only=False):
    accounts = {a["account_id"]: a for a in aiq.load("accounts.csv")}
    careers = {r["account_id"]: r for r in aiq.load("careers.csv")}
    sites = aiq.load("sites.csv")
    idx = aiq.build_site_index(sites)
    icp_by = {s["site_id"]: s.get("icp_fit") or "unknown" for s in sites}
    picks = aiq.load_overrides().get("site_picks", {})
    lk = aiq.lock(); lk.__enter__()          # held across the whole read-modify-write
    rows = aiq.load("postings.csv")
    by_id = {r["posting_id"]: r for r in rows}
    run_id = aiq.new_run_id("workday")
    total_new = 0

    for aid in (account_ids or []):
        acc = accounts.get(aid)
        if not acc:
            print("  %s: not an account" % aid)
            continue
        c = careers.get(aid, {})
        host = c.get("workday_host") or c.get("careers_host", "")
        tenant = site = None
        if "myworkdayjobs.com" in host:
            m = re.match(r"([a-z0-9\-]+)\.(wd\d)\.myworkdayjobs\.com", host)
            tenant = m.group(1) if m else None
            site = c.get("workday_site") or "external"
        if not tenant:
            host, tenant, _wd, site = discover(acc["company"], aiq.DOMAIN.get(aid, ""))
            if not tenant:
                print("  %-22s no Workday tenant found on the careers page" % aid)
                continue
            print("  %-22s discovered %s tenant=%s site=%s" % (aid, host, tenant, site))
        if discover_only:
            continue

        posts, total = fetch_all(host, tenant, site)
        n_new = 0
        for p in posts:
            path = p.get("externalPath") or ""
            url = "https://%s/%s%s" % (host, site, path)
            reqid = ""
            for b in (p.get("bulletFields") or []):
                if re.match(r"^[A-Za-z0-9\-]{4,}$", str(b)):
                    reqid = str(b)
                    break
            role_slug = re.sub(r"[^a-z0-9]+", "-", (p.get("title") or "").lower()).strip("-")
            pid = aiq.posting_id(aid, reqid, "", role_slug)
            city_raw = city_of(p.get("locationsText"))
            also = []
            if not city_raw and MULTI.match((p.get("locationsText") or "").strip()):
                city_raw, also = detail_locations(host, site, path)
                time.sleep(SLEEP)
            sid, res, cand = aiq.resolve_site(idx, aid, aiq.ckey(city_raw.split(",")[0]), picks)
            mac, msid, mbasis = aiq.mirror_for(idx, aid, aiq.ckey(city_raw.split(",")[0]))
            fam, frule, rank, srule, ex = aiq.classify(role_slug)
            pdate, pkind = rel_date(p.get("postedOn"))
            today = aiq.today()
            r = by_id.get(pid)
            if r is None:
                r = {"posting_id": pid, "account_id": aid,
                     "mirror_account_id": mac, "mirror_site_id": msid, "mirror_basis": mbasis,
                     "site_id": sid, "site_resolution": res, "site_candidates": "|".join(cand),
                     "city_slug": aiq.ckey(city_raw.split(",")[0]), "city_raw": city_raw,
                     "also_at": "|".join(also),
                     "icp_fit": icp_by.get(sid, "unknown"),
                     "role_title": p.get("title") or titleise(role_slug),
                     "role_title_source": "workday_api", "role_slug": role_slug, "req_id": reqid,
                     "url_form": "workday_api", "careers_host": host,
                     "job_family": fam, "job_family_rule": frule,
                     "seniority_rank": str(rank), "seniority_rule": srule, "excluded": ex,
                     "tier2_admit": "", "tier2_score": "", "url": url,
                     # A live API sighting is a far stronger date than a crawl timestamp.
                     "first_seen": pdate or today, "first_seen_kind": pkind or "live_observed",
                     "last_seen": today, "last_seen_kind": "live_observed", "seen_count": "1",
                     "period_first": aiq.quarter(pdate or today),
                     "period_last": aiq.quarter(today),
                     "posted_date": pdate, "posted_date_source": pkind,
                     "extraction_tier": "tier0_index", "body_chars": "", "extract_status": "",
                     "captured_at": today, "updated_at": today, "last_run_id": run_id}
                by_id[pid] = r
                rows.append(r)
                n_new += 1
            else:
                if r.get("last_run_id") != run_id:
                    r["seen_count"] = str(int(r.get("seen_count") or 0) + 1)
                    r["last_run_id"] = run_id
                r["last_seen"], r["last_seen_kind"] = aiq.today(), "live_observed"
                r["period_last"] = aiq.quarter(aiq.today())
                if pdate and not r.get("posted_date"):
                    r["posted_date"], r["posted_date_source"] = pdate, pkind
                if city_raw and not r.get("city_raw"):
                    r["city_raw"], r["also_at"] = city_raw, "|".join(also)
                    r["city_slug"] = aiq.ckey(city_raw.split(",")[0])
                r["updated_at"] = aiq.today()
        total_new += n_new
        print("  %-22s api total=%s fetched=%d new=%d" % (aid, total, len(posts), n_new))

        note = ("Workday CXS JSON API (tenant=%s site=%s). No Wayback history exists for Workday "
                "requisitions; the series accretes from here." % (tenant, site))
        prev = careers.get(aid)
        if prev and prev.get("probe_status") == "ok" and prev.get("pattern_kind") not in ("", "workday_api"):
            # ats_probe found an archive-crawlable careers host too (e.g. careers.bms.com). Keep it
            # for jobs_cdx and record the Workday tenant beside it.
            prev["workday_host"], prev["workday_site"] = host, site
            if "Workday CXS" not in (prev.get("notes") or ""):
                prev["notes"] = ((prev.get("notes") or "") + " | " + note).strip(" |")
        else:
            careers[aid] = {"account_id": aid, "company": acc["company"], "careers_host": host,
                            "pattern_kind": "workday_api", "req_path_glob": "",
                            "url_sample": (posts[0].get("externalPath") if posts else ""),
                            "req_urls_found": str(len(posts)), "city_from_url": "false",
                            "probe_status": "ok", "candidates_tried": host,
                            "probed_at": aiq.today(), "notes": note,
                            "workday_host": host, "workday_site": site}

    if discover_only:
        lk.__exit__(None, None, None)
        return
    rows = _rescore(rows, idx, icp_by, picks)
    aiq.write("postings.csv", rows, FIELDS)
    aiq.record_unmatched(rows)
    from ats_probe import FIELDS as CFIELDS
    aiq.write("careers.csv", list(careers.values()), CFIELDS)
    aiq.log_run({"run_id": run_id, "kind": "workday", "started_at": "",
                 "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "accounts": str(len(account_ids or [])), "urls_seen": "",
                 "new_urls": str(total_new), "extracted": "", "quarantined": "",
                 "verify_pass_rate": "", "notes": "workday cxs api sweep"})
    lk.__exit__(None, None, None)
    print("\npostings.csv: %d rows (%d new)" % (len(rows), total_new))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", action="append")
    ap.add_argument("--discover")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.discover:
        accounts = {x["account_id"]: x for x in aiq.load("accounts.csv")}
        acc = accounts.get(a.discover)
        if not acc:
            raise SystemExit("unknown account %r" % a.discover)
        print(discover(acc["company"], aiq.DOMAIN.get(a.discover, "")))
        return
    ids = a.account or []
    if a.all:
        ids = [r["account_id"] for r in aiq.load("careers.csv")
               if "myworkdayjobs.com" in (r.get("workday_host") or r.get("careers_host") or "")]
    if not ids:
        ap.print_help()
        return
    ingest(ids)
    rollup()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Test the "just scrape careers.<domain>" theory, per account, with numbers.

The theory is largely right and it is what the JSON-LD finding rests on. This measures where it holds
and where it does not, because three things vary by account and only one of them is effort:

  1. Does the site publish a SITEMAP listing live requisitions? (complete current coverage, free)
  2. Do requisition pages carry schema.org JobPosting markup? (structured datePosted + jobLocation +
     requisition id + full description -- strictly better than parsing prose)
  3. Does robots.txt permit it? Checked and recorded, because Invert would be doing this at scale
     under its own name and "it was public" is not the same as "we were allowed".

Where the theory breaks, measurably: Workday and SuccessFactors sites list NO requisitions in HTML at
all -- the reqs live behind a JS app, so there is no page to scrape. They do expose JSON APIs, which is
the same idea by a different door. And nothing here recovers history: scraping today's site gives
today. That is the Hillerod problem, and no amount of scraping fixes it.

  python3 careers_probe.py --account gsk roche takeda
  python3 careers_probe.py --bucket history_available
  python3 careers_probe.py --report

Writes data/careers_probe.csv.
"""
import argparse, gzip, json, re, time
import urllib.error, urllib.parse, urllib.request

import aiq

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
FIELDS = ["account_id", "company", "careers_host", "robots", "robots_allows_jobs",
          "sitemap_url", "sitemap_job_urls", "jsonld_sampled", "jsonld_hits",
          "date_posted_sample", "location_structured", "req_identifier", "body_chars_median",
          "verdict", "notes", "probed_at"]

SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-jobs.xml", "/jobs-sitemap.xml",
                 "/sitemap/sitemap-index.xml"]
JOBISH = re.compile(r"/job/|/jobs/|/job-detail|/vacancy|/position/", re.I)


def safe_url(u):
    """
    Percent-encode the path/query. Real sitemaps contain non-ASCII (a curly apostrophe in a French
    site name killed the first run), and urllib encodes the request line as ASCII.
    """
    sp = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit((
        sp.scheme, sp.netloc.encode("idna").decode("ascii") if any(ord(c) > 127 for c in sp.netloc)
        else sp.netloc,
        urllib.parse.quote(sp.path, safe="/%:@&=+$,~*!()'"),
        urllib.parse.quote(sp.query, safe="/?&=%:@+$,~*!()'"), ""))


def get(url, timeout=25):
    r = urllib.request.Request(safe_url(url), headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        b = f.read(4000000)
        if url.endswith(".gz"):
            try:
                b = gzip.decompress(b)
            except OSError:
                pass
        return f.status, b.decode("utf-8", "replace")


def robots_check(host):
    """-> (raw_summary, allows_jobs). Conservative: an explicit Disallow covering job paths = False."""
    try:
        _c, t = get("https://%s/robots.txt" % host, timeout=15)
    except Exception:
        return "no robots.txt", "unknown"
    lines = [l.strip() for l in t.splitlines() if l.strip() and not l.strip().startswith("#")]
    star, cur = [], None
    for l in lines:
        m = re.match(r"(?i)user-agent:\s*(\S+)", l)
        if m:
            cur = m.group(1)
            continue
        if cur == "*":
            star.append(l)
    dis = [re.sub(r"(?i)disallow:\s*", "", l) for l in star if re.match(r"(?i)disallow:", l)]
    blocked = any(d == "/" or JOBISH.search(d or "") for d in dis if d)
    summary = ("%d directive(s) for *; disallow: %s" % (len(star), ", ".join(d for d in dis if d)[:80])
               if star else "no rules for *")
    return summary, ("false" if blocked else "true")


def find_sitemap(host):
    """-> (sitemap_url, [job urls]). Follows one level of sitemap index."""
    seen_idx = []
    try:
        _c, rt = get("https://%s/robots.txt" % host, timeout=15)
        seen_idx += re.findall(r"(?i)sitemap:\s*(\S+)", rt)
    except Exception:
        pass
    seen_idx += ["https://%s%s" % (host, p) for p in SITEMAP_PATHS]
    for sm in seen_idx[:8]:
        try:
            _c, t = get(sm)
        except Exception:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", t)
        if not locs:
            continue
        jobs = [l for l in locs if JOBISH.search(l)]
        if jobs:
            return sm, jobs
        # sitemap index -> descend one level into the most job-looking child
        kids = [l for l in locs if l.endswith((".xml", ".xml.gz"))]
        for k in kids[:6]:
            try:
                _c2, t2 = get(k)
            except Exception:
                continue
            l2 = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", t2)
            j2 = [l for l in l2 if JOBISH.search(l)]
            if j2:
                return k, j2
    return "", []


def jobposting_ld(html):
    for m in re.finditer(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html):
        blob = m.group(1)
        if "JobPosting" not in blob:
            continue
        try:
            d = json.loads(blob)
        except ValueError:
            continue
        if isinstance(d, list):
            d = next((x for x in d if isinstance(x, dict) and x.get("@type") == "JobPosting"), None)
        if isinstance(d, dict) and (d.get("@type") == "JobPosting" or d.get("title")):
            return d
    return None


def sample_jobs(urls, n=4):
    """-> (hits, dates, locs, ids, body_lens)"""
    hits, dates, locs, ids, lens = 0, [], 0, 0, []
    step = max(1, len(urls) // n)
    for u in urls[::step][:n]:
        try:
            _c, h = get(u)
        except Exception:          # one malformed sitemap entry must not kill the whole probe
            continue
        ld = jobposting_ld(h)
        if not ld:
            continue
        hits += 1
        if ld.get("datePosted"):
            dates.append(str(ld["datePosted"]))
        jl = ld.get("jobLocation")
        jl = jl[0] if isinstance(jl, list) and jl else jl
        if isinstance(jl, dict) and (jl.get("address") or {}).get("addressLocality"):
            locs += 1
        if ld.get("identifier"):
            ids += 1
        d = ld.get("description") or ""
        lens.append(len(d if isinstance(d, str) else json.dumps(d)))
        time.sleep(0.4)
    return hits, dates, locs, ids, lens


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", nargs="*")
    ap.add_argument("--bucket")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    prev = {r["account_id"]: r for r in aiq.load("careers_probe.csv")}
    if a.report:
        rows = list(prev.values())
        if not rows:
            print("nothing probed yet"); return
        from collections import Counter
        print("careers-site probe: %d account(s)\n" % len(rows))
        print("  %-22s %-7s %-9s %-8s %s" % ("account", "sitemap", "jobs", "json-ld", "verdict"))
        for r in sorted(rows, key=lambda x: x["account_id"]):
            print("  %-22s %-7s %-9s %-8s %s"
                  % (r["account_id"], "yes" if r["sitemap_url"] else "no",
                     r["sitemap_job_urls"] or "0",
                     "%s/%s" % (r["jsonld_hits"], r["jsonld_sampled"]), r["verdict"]))
        print("\nverdicts:", dict(Counter(r["verdict"] for r in rows)))
        ok = [r for r in rows if r["verdict"] == "scrapeable_structured"]
        if ok:
            print("\nscrapeable + structured (%d): %s" % (len(ok), ", ".join(r["account_id"] for r in ok)))
            print("  total live req URLs discoverable from sitemaps: %d"
                  % sum(int(r["sitemap_job_urls"] or 0) for r in ok))
        return

    census = {r["account_id"]: r for r in aiq.load("ats_census.csv")}
    accounts = aiq.load("accounts.csv")
    targets = []
    for x in accounts:
        if x["active"] != "true" or x["in_universe"] != "true":
            continue
        aid = x["account_id"]
        if a.account and aid not in a.account:
            continue
        if a.bucket and (census.get(aid, {}).get("bucket") != a.bucket):
            continue
        targets.append(x)

    print("probing %d account(s)\n" % len(targets))
    for x in targets:
        aid = x["account_id"]
        cen = census.get(aid, {})
        host = ""
        for src in (cen.get("careers_url", ""), ):
            if src:
                host = urllib.parse.urlsplit(src).netloc
        if not host:
            print("  %-22s no careers host on file (run ats_census.py)" % aid); continue
        rb, allows = robots_check(host)
        try:
            sm, jobs = find_sitemap(host)
        except Exception as e:
            sm, jobs = "", []
            print("      sitemap probe failed: %s" % str(e)[:60])
        hits = sampled = locs = ids = 0
        dates, lens = [], []
        if jobs:
            try:
                hits, dates, locs, ids, lens = sample_jobs(jobs)
            except Exception as e:
                print("      job sampling failed: %s" % str(e)[:60])
            sampled = min(4, len(jobs))
        if jobs and hits:
            verdict = "scrapeable_structured"
        elif jobs:
            verdict = "scrapeable_unstructured"
        else:
            verdict = "no_html_listing"      # almost always a JS app -> use its JSON API instead
        row = {"account_id": aid, "company": x["company"], "careers_host": host,
               "robots": rb, "robots_allows_jobs": allows, "sitemap_url": sm,
               "sitemap_job_urls": str(len(jobs)), "jsonld_sampled": str(sampled),
               "jsonld_hits": str(hits),
               "date_posted_sample": " | ".join(dates[:3]),
               "location_structured": str(locs), "req_identifier": str(ids),
               "body_chars_median": str(sorted(lens)[len(lens) // 2] if lens else 0),
               "verdict": verdict, "notes": "", "probed_at": aiq.today()}
        prev[aid] = row
        print("  %-22s sitemap=%-4s jobs=%-6s json-ld=%s/%s  robots_ok=%-7s %s"
              % (aid, "yes" if sm else "no", len(jobs), hits, sampled, allows, verdict))
        if dates:
            print("      datePosted sample: %s" % ", ".join(dates[:3]))
        aiq.write("careers_probe.csv", list(prev.values()), FIELDS)
    print("\nwrote data/careers_probe.csv (%d rows)" % len(prev))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Fetch and cache the BODY text of Tier-2-admitted job requisitions. Python owns this step, not an
agent, for one reason: if we hold the bytes, quote verification is an exact substring test against
our own copy instead of trust in what a model says it read.

Source order per requisition:
  1. the live URL           -> tier3_live_body   (open reqs; richest, has a posted date)
  2. Wayback, fattest first -> tier2_archive_body
  3. nothing usable         -> tier1_shell / extract_status=body_unavailable, which is a VISIBLE
                               gap, never a silent drop

Stage 0 taught us to take targets from the CDX corpus rather than arbitrary live URLs: 4 of 6 reqs
sourced from live pages 404'd with no archive at all, while URLs discovered through CDX have a
snapshot by construction. It also taught us to try EVERY capture of a URL, not just one -- a single
timestamp miss looks identical to "never archived".

  python3 fetch_bodies.py --account astrazeneca            # all admitted, best score first
  python3 fetch_bodies.py --account biogen --limit 40
  python3 fetch_bodies.py --stats

Writes data/cache/bodies/<posting_id>.txt and updates postings.csv in place.
"""
import argparse, html, json, os, re, time
import urllib.error, urllib.request

import aiq
from ats_probe import cdx
from jobs_cdx import FIELDS

BODIES = os.path.join(aiq.CACHE, "bodies")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_BODY = 1500          # below this it is nav/footer chrome, not a job description
SLEEP = 0.35

# A careers site that has delisted a req often returns 200 or 404 with a generic "this job has
# expired" page. Stage 0 measured AstraZeneca's at 848 chars of pure nav. Detect and reject.
EXPIRED = re.compile(r"job (?:posting )?(?:has )?(?:expired|been filled|no longer)|custom job error"
                     r"|position is no longer|this job is no longer", re.I)


def clean(h):
    """HTML -> comparable plain text. Entities are unescaped ONCE here so a quote containing
    'MS&T' matches a page that stored 'MS&amp;T'."""
    t = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer)[^>]*>.*?</\1>", " ", h)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def get(url, timeout=35):
    r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return f.read().decode("utf-8", "replace")


def captures(url, limit=25):
    """All Wayback captures of one URL, fattest first -- length is the best proxy for a real body."""
    rows = cdx({"url": url, "output": "json", "fl": "timestamp,length", "limit": str(limit)})
    out = []
    for c in (rows[1:] if rows else []):
        if c and len(c) >= 2:
            try:
                out.append((c[0], int(c[1] or 0)))
            except ValueError:
                out.append((c[0], 0))
    out.sort(key=lambda x: -x[1])
    return out


def workday_body(url):
    """
    Workday requisition pages are JS-rendered, so an HTML fetch returns nav chrome and the archive
    has nothing at all. The same CXS API that lists the jobs also serves each description as JSON:
    https://<host>/wday/cxs/<tenant>/<site>/job/<path>. This is a live, first-party source, so it
    lands at tier3 and carries a real startDate.
    """
    m = re.match(r"https://([^/]+)/([^/]+)(/job/.+)$", url)
    if not m:
        return "", ""
    host, site, path = m.group(1), m.group(2), m.group(3)
    tenant = host.split(".")[0]
    api = "https://%s/wday/cxs/%s/%s%s" % (host, tenant, site, path)
    try:
        r = urllib.request.Request(api, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(r, timeout=35) as f:
            d = json.loads(f.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return "", ""
    info = d.get("jobPostingInfo") or {}
    parts = [info.get("title") or "", info.get("location") or "",
             "Posted " + (info.get("startDate") or ""), info.get("jobDescription") or ""]
    return clean(" ".join(parts)), (info.get("startDate") or "")


def fetch_one(row):
    """-> (text, tier, status, note)"""
    url = row["url"]
    # 0. Workday: the JSON API is the only place the description exists
    if row.get("url_form") == "workday_api" or "myworkdayjobs.com" in url:
        t, sd = workday_body(url)
        if len(t) >= MIN_BODY:
            return t, "tier3_live_body", "ok", "workday cxs api" + (" " + sd if sd else "")
        return "", "tier1_shell", "body_unavailable", "workday cxs returned %dch" % len(t)
    # 1. live
    try:
        h = get(url)
        t = clean(h)
        if len(t) >= MIN_BODY and not EXPIRED.search(t[:4000]):
            return t, "tier3_live_body", "ok", "live"
        live_note = "live returned %dch%s" % (len(t), " (expired page)" if EXPIRED.search(t[:4000]) else "")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        live_note = "live %s" % str(e)[:40]

    # 2. archive, fattest capture first
    caps = captures(url)
    for ts, _ln in caps[:4]:
        for form in ("id_/", "/"):
            try:
                t = clean(get("https://web.archive.org/web/%s%s%s" % (ts, form, url)))
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                continue
            if len(t) >= MIN_BODY and not EXPIRED.search(t[:4000]):
                return t, "tier2_archive_body", "ok", "archive %s" % ts
        time.sleep(SLEEP)
    return "", "tier1_shell", "body_unavailable", "%s; %d archive captures, none usable" % (
        live_note, len(caps))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", action="append")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    os.makedirs(BODIES, exist_ok=True)
    rows = aiq.load("postings.csv")
    by_id = {r["posting_id"]: r for r in rows}

    if a.stats:
        from collections import Counter
        have = {f[:-4] for f in os.listdir(BODIES) if f.endswith(".txt")}
        adm = [r for r in rows if r["tier2_admit"] == "true"]
        print("admitted: %d | bodies cached: %d" % (len(adm), len(have & set(by_id))))
        print("tiers:   ", dict(Counter(r["extraction_tier"] for r in adm)))
        print("status:  ", dict(Counter(r["extract_status"] or "(none)" for r in adm)))
        sizes = []
        for pid in have:
            try:
                sizes.append(os.path.getsize(os.path.join(BODIES, pid + ".txt")))
            except OSError:
                pass
        if sizes:
            sizes.sort()
            print("body chars: min=%d median=%d max=%d" % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
        return

    updates = {}
    targets = [r for r in rows if r["tier2_admit"] == "true"
               and (not a.account or r["account_id"] in a.account)
               and int(r["tier2_score"] or 0) >= a.min_score]
    targets.sort(key=lambda r: -int(r["tier2_score"] or 0))
    if a.limit:
        targets = targets[:a.limit]

    print("fetching bodies for %d admitted req(s)" % len(targets))
    ok = shell = cached = 0
    for i, r in enumerate(targets, 1):
        path = os.path.join(BODIES, r["posting_id"] + ".txt")
        if os.path.exists(path) and not a.refetch and os.path.getsize(path) >= MIN_BODY:
            cached += 1
            continue
        text, tier, status, note = fetch_one(r)
        if text:
            open(path, "w", encoding="utf-8").write(text)
            ok += 1
        else:
            shell += 1
        r["body_chars"] = str(len(text))
        r["extraction_tier"] = tier
        r["extract_status"] = status
        # A live fetch is the only place a real posted date can appear.
        if tier == "tier3_live_body" and not r.get("posted_date"):
            m = re.search(r"[Pp]osted\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})", text)
            if m:
                r["posted_date"] = m.group(1)
                r["posted_date_source"] = "body_posted_date"
        r["updated_at"] = aiq.today()
        updates[r["posting_id"]] = {k: r[k] for k in
                                    ("body_chars", "extraction_tier", "extract_status",
                                     "posted_date", "posted_date_source", "updated_at")}
        if i % 10 == 0 or i == len(targets):
            print("  %3d/%d  ok=%d shell=%d cached=%d   %-13s %s"
                  % (i, len(targets), ok, shell, cached, tier.replace("tier", "t"), r["role_slug"][:38]))
            # Patch, never snapshot: another collector may have added rows since we loaded.
            aiq.patch("postings.csv", FIELDS, updates)
            updates = {}
        time.sleep(SLEEP)

    if updates:
        aiq.patch("postings.csv", FIELDS, updates)
    tot = ok + shell
    print("\nbodies: %d usable / %d unavailable (%d already cached) -- %d%% yield"
          % (ok, shell, cached, (100 * ok // tot) if tot else 0))


if __name__ == "__main__":
    main()

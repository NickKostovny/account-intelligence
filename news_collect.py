#!/usr/bin/env python3
"""News collector: recent public news items per account and per site, as DATA only.

Wishlist: "company-wide pipeline and business news" and "site-specific program news".

Source is Google News RSS (free, no key). Nothing is classified here and no paid or LLM API is
called; a later step reads data/news/news_items.csv and decides what matters.

Scope:
  accounts  data/accounts.csv rows with in_universe=true, active=true, exclude=false.
  sites     data/sites.csv rows whose verdict (data/jev/site_fit.csv policy_verdict, else
            jev_verdict) is KEEP or FLAG. Rows that are closed or sold (jev_verdict CLOSED, status
            closed / sold_or_divested) or manually dropped (verdict_manual=Drop) are not queried.
            Max 6 site queries per account, KEEP first. Sites of one account that share a city
            share one query; their ids are joined with ';' in site_id.

Queries (one RSS request each):
  account   "<company>" (plant OR facility OR site OR manufacturing OR expansion OR layoffs
            OR acquisition) after:<run date - 18 months>
  site      "<company>" "<city>" after:<run date - 18 months>
The feed reads `when:18m` as 18 MINUTES (0 items), so the window is `after:` plus a date filter in
code. Items older than 18 months before the run date are dropped.

Cache: raw XML at data/cache/news/<sha1>.xml, keyed on the query WITHOUT the after: date, so a
rerun on a later day costs no requests. --refresh ignores the cache. fetched_at = cache file mtime.

Output: data/news/news_items.csv (one row per item per account, deduped by item_id = sha1(link);
a site row wins over the account row for the same item) and data/news/news_queries.csv (one row
per query: status, items, cached or fetched).

A 429 or 503 stops all further requests; the rows already collected are still written and the
blocked query is reported. Exit code 2 in that case.

  python3 news_collect.py                 # all in-scope accounts
  python3 news_collect.py biogen amgen    # named accounts only
  python3 news_collect.py --refresh       # ignore the cache
"""
import csv, email.utils, hashlib, html, os, random, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone

import aiq

NEWS_DIR = os.path.join(aiq.DATA, "news")
NEWS_CACHE = os.path.join(aiq.CACHE, "news")
FEED = "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/129.0.0.0 Safari/537.36")
WINDOW_DAYS = 548                # 18 months
MAX_SITE_QUERIES = 6
SLEEP = (1.0, 1.5)
BLOCK_CODES = {429, 503}
ACCOUNT_TERMS = ("(plant OR facility OR site OR manufacturing OR expansion OR layoffs "
                 "OR acquisition)")

# Name used in the query when accounts.csv's display name is not what the press writes.
QUERY_NAME = {"merck-and-co": "Merck & Co", "fujifilm": "Fujifilm Diosynth"}

ITEM_FIELDS = ["item_id", "account_id", "query_kind", "site_id", "title", "source", "url",
               "published", "snippet", "fetched_at"]
QUERY_FIELDS = ["account_id", "query_kind", "site_id", "query", "cache_key", "status",
                "http_status", "items_in_feed", "items_in_window", "fetched_at"]

class Blocked(Exception):
    pass

def truthy(v):
    return (v or "").strip().lower() == "true"

def in_scope_accounts(only=None):
    rows = [a for a in aiq.load("accounts.csv")
            if truthy(a["in_universe"]) and truthy(a["active"]) and not truthy(a["exclude"])]
    if only:
        rows = [a for a in rows if a["account_id"] in only]
    return rows

def site_verdicts():
    """site_id -> (verdict, skip_reason). verdict = policy_verdict, else jev_verdict."""
    out = {}
    for r in aiq.load("jev/site_fit.csv"):
        v = (r.get("policy_verdict") or r.get("jev_verdict") or "").upper()
        skip = ""
        if (r.get("jev_verdict") or "").upper() == "CLOSED" or r.get("status") in (
                "closed", "sold_or_divested"):
            skip = "closed"
        out[r["site_id"]] = (v, skip)
    return out

def city_proper(raw):
    """'Cambridge / Waltham, MA' -> 'Cambridge'; 'Boston (Allston), MA' -> 'Boston'; 'n/a' -> ''."""
    c = re.sub(r"\([^)]*\)", "", (raw or "").split(",")[0]).split("/")[0].strip()
    if not c or c.lower() in ("n/a", "na", "tbd", "unknown") or c.lower().startswith("various"):
        return ""
    return c

def site_targets(account_id, sites, verdicts):
    """Up to MAX_SITE_QUERIES (city, [site_ids]) for one account, KEEP before FLAG."""
    picks = OrderedDict()
    for want in ("KEEP", "FLAG"):
        for s in sites:
            if s["account_id"] != account_id or (s.get("verdict_manual") or "").lower() == "drop":
                continue
            v, skip = verdicts.get(s["site_id"], ("", ""))
            city = city_proper(s["city"])
            if v != want or skip or not city:
                continue
            k = aiq.ckey(city)
            if k in picks:
                picks[k][1].append(s["site_id"])
            elif len(picks) < MAX_SITE_QUERIES:
                picks[k] = (city, [s["site_id"]])
    return list(picks.values())

def cache_path(query_no_date):
    return os.path.join(NEWS_CACHE, hashlib.sha1(query_no_date.encode("utf-8")).hexdigest() + ".xml")

def fetch(query, query_no_date, refresh, stats):
    """Return (xml_bytes, status, http_status, fetched_at). Raises Blocked on 429/503."""
    p = cache_path(query_no_date)
    if os.path.exists(p) and not refresh:
        return open(p, "rb").read(), "cached", "", mtime_iso(p)
    if stats["requests"]:
        time.sleep(random.uniform(*SLEEP))
    url = FEED % urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en"})
    stats["requests"] += 1
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        if e.code in BLOCK_CODES:
            raise Blocked("HTTP %d on: %s" % (e.code, query))
        return b"", "http_error", str(e.code), ""
    except (urllib.error.URLError, OSError) as e:
        return b"", "net_error:%s" % type(e).__name__, "", ""
    os.makedirs(NEWS_CACHE, exist_ok=True)
    with open(p, "wb") as f:
        f.write(body)
    return body, "fetched", str(code), mtime_iso(p)

def mtime_iso(p):
    return datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def iso_date(pub):
    try:
        return email.utils.parsedate_to_datetime(pub).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ""

def parse_items(xml_bytes):
    """RSS <item>s -> dicts with title, source, url, published, snippet."""
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        if not link:
            continue
        source = (it.findtext("source") or "").strip()
        title = strip_html(it.findtext("title"))
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        out.append({"title": title, "source": source, "url": link,
                    "published": iso_date(it.findtext("pubDate")),
                    "snippet": strip_html(it.findtext("description"))[:500]})
    return out

def run_query(acct, kind, site_ids, query_no_date, since, refresh, stats, qlog):
    query = "%s after:%s" % (query_no_date, since)
    body, status, code, fetched_at = fetch(query, query_no_date, refresh, stats)
    items = parse_items(body)
    kept = [dict(i, item_id=hashlib.sha1(i["url"].encode("utf-8")).hexdigest(),
                 account_id=acct, query_kind=kind, site_id=";".join(site_ids),
                 fetched_at=fetched_at)
            for i in items if i["published"] and i["published"] >= since]
    qlog.append({"account_id": acct, "query_kind": kind, "site_id": ";".join(site_ids),
                 "query": query, "cache_key": os.path.basename(cache_path(query_no_date))[:-4],
                 "status": status, "http_status": code, "items_in_feed": len(items),
                 "items_in_window": len(kept), "fetched_at": fetched_at})
    return kept

def merge(by_id, rows):
    """Dedupe within an account: first seen wins, except a site row replaces an account row."""
    for r in rows:
        prev = by_id.get(r["item_id"])
        if prev is None or (prev["query_kind"] == "account" and r["query_kind"] == "site"):
            by_id[r["item_id"]] = r

def collect_account(a, sites, verdicts, since, refresh, stats, qlog):
    acct = a["account_id"]
    name = QUERY_NAME.get(acct, a["company"])
    by_id = OrderedDict()
    merge(by_id, run_query(acct, "account", [], '"%s" %s' % (name, ACCOUNT_TERMS),
                           since, refresh, stats, qlog))
    for city, ids in site_targets(acct, sites, verdicts):
        merge(by_id, run_query(acct, "site", ids, '"%s" "%s"' % (name, city),
                               since, refresh, stats, qlog))
    return list(by_id.values())

def main(argv):
    refresh = "--refresh" in argv
    only = [x for x in argv if not x.startswith("--")]
    t0 = time.time()
    since = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    accounts = in_scope_accounts(only)
    sites, verdicts = aiq.load("sites.csv"), site_verdicts()
    stats, qlog, rows, blocked = {"requests": 0}, [], [], ""
    for a in accounts:
        try:
            rows += collect_account(a, sites, verdicts, since, refresh, stats, qlog)
        except Blocked as e:
            blocked = "%s (account %s)" % (e, a["account_id"])
            break
    rows.sort(key=lambda r: (r["account_id"], r["query_kind"], r["site_id"], r["published"]),
              reverse=False)
    aiq.write("news/news_items.csv", rows, ITEM_FIELDS)
    aiq.write("news/news_queries.csv", qlog, QUERY_FIELDS)
    report(accounts, rows, qlog, stats, since, time.time() - t0, blocked)
    return 2 if blocked else 0

def report(accounts, rows, qlog, stats, since, secs, blocked):
    by_acct = Counter(r["account_id"] for r in rows)
    by_site = Counter(r["account_id"] for r in rows if r["query_kind"] == "site")
    nq = Counter(q["account_id"] for q in qlog)
    print("%-24s %7s %6s %6s" % ("account", "queries", "items", "site"))
    for a in accounts:
        k = a["account_id"]
        print("%-24s %7d %6d %6d" % (k, nq[k], by_acct[k], by_site[k]))
    st = Counter(q["status"] for q in qlog)
    print("\nwindow: published >= %s" % since)
    print("queries: %d (account %d, site %d) | %s" % (
        len(qlog), sum(q["query_kind"] == "account" for q in qlog),
        sum(q["query_kind"] == "site" for q in qlog), dict(st)))
    print("items: %d (account %d, site %d) across %d accounts; zero-item queries: %d" % (
        len(rows), sum(r["query_kind"] == "account" for r in rows),
        sum(r["query_kind"] == "site" for r in rows), len(by_acct),
        sum(q["items_in_window"] == 0 for q in qlog)))
    print("requests made: %d | runtime %.0fs" % (stats["requests"], secs))
    print("BLOCKED: " + blocked if blocked else "blocked: none")
    print("wrote data/news/news_items.csv, data/news/news_queries.csv")

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

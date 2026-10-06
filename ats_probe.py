#!/usr/bin/env python3
"""
Discover, per account, which careers host actually yields job-requisition URLs in the Wayback
index, and how to parse city / role / req-id out of those URLs.

Deterministic and cheap: one Wayback CDX call per candidate host, cached to disk. No LLM. Accounts
that come back empty are written with probe_status=needs_discovery rather than guessed at, so the
gap is visible and a small LLM fallback batch can target only the misses.

  python3 ats_probe.py                 # probe every account with no cached result
  python3 ats_probe.py --account biogen # probe one
  python3 ats_probe.py --refresh-stale  # re-probe anything older than 30 days
  python3 ats_probe.py --report         # print the coverage table, no network

Writes data/careers.csv.
"""
import argparse, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request

import aiq

CDX = "http://web.archive.org/cdx/search/cdx"
UA = "invert-account-intel/1.0 (GTM research; contact nick@invertbio.com)"
CACHE = os.path.join(aiq.CACHE, "ats")
STALE_DAYS = 30

FIELDS = ["account_id", "company", "careers_host", "pattern_kind", "req_path_glob",
          "url_sample", "req_urls_found", "city_from_url", "probe_status", "candidates_tried",
          "probed_at", "notes", "workday_host", "workday_site"]

# Seed candidates per account. Multiple guesses are fine -- the probe decides which is real. An
# account absent from here still gets the generic guesses derived from its name.
SEED = {
    "abbvie": ["careers.abbvie.com"],
    "agc-biologics": ["agcbio.com", "careers.agcbio.com"],
    "alexion": ["careers.astrazeneca.com", "alexion.com"],
    "alnylam": ["careers.alnylam.com", "alnylam.com"],
    "amgen": ["careers.amgen.com"],
    "ascendis-pharma": ["ascendispharma.com", "careers.ascendispharma.com"],
    "astellas": ["careers.astellas.com", "astellas.com"],
    "astrazeneca": ["careers.astrazeneca.com"],
    "bayer": ["career.bayer.com", "careers.bayer.com"],
    "beigene": ["careers.beigene.com", "beigene.com", "careers.beonemedicines.com"],
    # Workday tenant is "biibhr", not "biogen" -- found by reading the ATS host out of
    # biogen.com/careers.html. Guessing tenant names from company names does not work.
    "biogen": ["biibhr.wd3.myworkdayjobs.com", "careers.biogen.com", "biogen.com"],
    "biomarin": ["careers.biomarin.com", "biomarin.com"],
    "biontech": ["biontech.com", "jobs.biontech.com"],
    "boehringer-ingelheim": ["jobs.boehringer-ingelheim.com", "careers.boehringer-ingelheim.com"],
    "bristol-myers-squibb": ["careers.bms.com", "bms.com"],
    "chugai": ["chugai-pharm.co.jp", "chugai-pharm.com"],
    "csl": ["careers.csl.com", "csl.com"],
    "daiichi-sankyo": ["daiichisankyo.wd1.myworkdayjobs.com", "daiichisankyo.com",
                       "careers.daiichisankyo.com"],
    "eisai": ["careers.eisai.com", "eisai.com"],
    "eli-lilly": ["careers.lilly.com", "lilly.com"],
    "fujifilm": ["fujifilmdiosynth.com", "careers.fujifilm.com"],
    "genentech": ["careers.gene.com", "gene.com"],
    "genmab": ["careers.genmab.com", "genmab.com"],
    "gilead-sciences": ["careers.gilead.com", "gilead.wd1.myworkdayjobs.com", "gilead.com"],
    "gsk": ["jobs.gsk.com", "gsk.com"],
    "incyte": ["careers.incyte.com", "incyte.com"],
    "ipsen": ["careers.ipsen.com", "ipsen.com"],
    "jazz-pharmaceuticals": ["careers.jazzpharma.com", "jazzpharma.com"],
    "johnson-johnson": ["jobs.jnj.com", "careers.jnj.com"],
    "lonza": ["careers.lonza.com", "lonza.com"],
    "lundbeck": ["careers.lundbeck.com", "lundbeck.com"],
    "merck-and-co": ["jobs.merck.com", "merck.com"],
    "merck-kgaa": ["careers.emdgroup.com", "emdgroup.com", "merckgroup.com"],
    "moderna": ["careers.modernatx.com", "modernatx.wd1.myworkdayjobs.com", "modernatx.com"],
    "novartis": ["careers.novartis.com", "novartis.com"],
    "novavax": ["novavax.com", "careers.novavax.com"],
    "novo-nordisk": ["careers.novonordisk.com", "novonordisk.com"],
    "otsuka": ["careers.otsuka-us.com", "otsuka.co.jp"],
    "pfizer": ["pfizer.wd1.myworkdayjobs.com", "careers.pfizer.com", "pfizer.com"],
    "recipharm": ["recipharm.com", "careers.recipharm.com"],
    "regeneron": ["careers.regeneron.com", "regeneron.com"],
    "roche": ["careers.roche.com", "roche.com"],
    "samsung-biologics": ["samsungbiologics.com"],
    "sanofi": ["en.jobs.sanofi.com", "jobs.sanofi.com", "sanofi.com"],
    "seagen": ["careers.pfizer.com", "seagen.com"],
    "takeda": ["jobs.takeda.com", "takeda.wd3.myworkdayjobs.com", "takeda.com"],
    "ucb": ["careers.ucb.com", "ucb.com"],
    "vertex": ["careers.vrtx.com", "vrtx.wd5.myworkdayjobs.com", "vrtx.com"],
    "zoetis": ["jobs.zoetis.com", "zoetis.com"],
}

# ---------------------------------------------------------------- URL shape recognition
#
# Each kind knows how to pull (city_slug, role_slug, req_id) out of a req URL. Adding an ATS means
# adding one entry here plus one branch in parse_req_url().
SHAPES = [
    # careers.astrazeneca.com/job/gaithersburg/director-operations-it/7684/91401022464
    ("phenom", re.compile(r"/job/(?P<city>[^/]+)/(?P<role>[^/]+)/(?P<board>\d+)/(?P<req>[\d/]+)/?$")),
    # x.wdN.myworkdayjobs.com/en-US/Site/job/Cambridge-MA/Data-Engineer_R-12345
    ("workday", re.compile(r"/job/(?P<city>[^/]+)/(?P<role>[^/]+?)_(?P<req>[A-Z0-9\-]+)/?$")),
    # jobs.example.com/job/12345/senior-scientist-cambridge-ma
    ("id_first", re.compile(r"/jobs?/(?P<req>\d{4,})/(?P<role>[^/]+)/?$")),
    # careers.example.com/jobs/senior-scientist-cambridge-ma-12345
    ("slug_tail_id", re.compile(r"/jobs?/(?P<role>[^/]*?)-(?P<req>\d{4,})/?$")),
    # generic /job/<slug> with no city and no id
    ("slug_only", re.compile(r"/jobs?/(?P<role>[^/]{8,})/?$")),
]
# A URL must look like a single requisition, not a search or category page.
NOT_A_REQ = re.compile(r"/(search|results|category|categories|location|locations|sitemap|feed|rss"
                       r"|apply|login|register|talent|events?|students?|faq)\b|\?", re.I)

def shape_of(path):
    for kind, rx in SHAPES:
        m = rx.search(path)
        if m:
            return kind, m
    return "", None

def parse_req_url(url, pattern_kind=""):
    """
    -> dict(city_slug, role_slug, req_id, url_form) or None if this is not a requisition URL.
    pattern_kind, when supplied, is tried first; we still fall back so a host that mixes shapes
    (they do) still parses.
    """
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    path = p.path
    if not path or NOT_A_REQ.search(url):
        return None
    order = SHAPES
    if pattern_kind:
        order = [s for s in SHAPES if s[0] == pattern_kind] + [s for s in SHAPES if s[0] != pattern_kind]
    for kind, rx in order:
        m = rx.search(path)
        if not m:
            continue
        g = m.groupdict()
        req = (g.get("req") or "").strip("/")
        # Phenom emits BOTH /7684/<reqid> and a legacy /7684/<m>/<d>/<year> form for the same req.
        # The date form is a duplicate; mark it so the ingester can collapse the pair.
        form = kind
        if kind == "phenom" and "/" in req:
            form = "phenom_datepath"
            req = ""
        return {
            "city_slug": aiq.slug_to_key(g.get("city") or ""),
            "city_raw": urllib.parse.unquote(g.get("city") or ""),
            "role_slug": re.sub(r"[^a-z0-9\-]+", "-", urllib.parse.unquote(g.get("role") or "").lower()).strip("-"),
            "req_id": req,
            "url_form": form,
        }
    return None

# ---------------------------------------------------------------- CDX

def cdx(params, tries=3, timeout=90):
    """GET the CDX index. Returns the parsed array-of-arrays (header row included) or []."""
    url = CDX + "?" + urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace").strip()
            if not raw:
                return []
            # The whole response is ONE json array; parsing line-by-line fails on the trailing
            # commas. This bit the previous session.
            return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
            if i == tries - 1:
                print("      cdx failed: %s" % e)
                return []
            time.sleep(2 * (i + 1))
    return []

# Requisition path globs, tried per host. Probing `<host>/*` instead samples the index
# alphabetically, and the first few hundred URLs of a careers site are landing and category pages
# (/barcelona-jobs, /belgium-jobs, ...) -- which is how a first pass picked jobs.astrazeneca.com
# with 2 hits over careers.astrazeneca.com with 9,844. Anchoring on the req path fixes that and
# makes the later full sweep far cheaper, since the glob is stored and reused.
REQ_GLOBS = ["/job/*", "/jobs/*", "/en-US/*", "/en/job/*", "/job-detail/*", "/careers/job/*",
             "/global/en/job/*"]

def probe_path(host, glob, limit=500):
    """-> (n_parseable, kind_counts, sample_url)"""
    rows = cdx({"url": host + glob, "output": "json", "from": "20240101",
                "collapse": "urlkey", "fl": "original", "limit": str(limit)})
    urls = [r[0] for r in rows[1:] if r and str(r[0]).startswith("http")] if rows else []
    kinds, sample, n = {}, "", 0
    for u in urls:
        got = parse_req_url(u)
        if got and (got["req_id"] or got["url_form"] in ("slug_only", "phenom_datepath")):
            n += 1
            kinds[got["url_form"]] = kinds.get(got["url_form"], 0) + 1
            if not sample:
                sample = u
    return n, kinds, sample

def probe_host(host):
    """-> (req_urls_found, pattern_kind, sample_url, req_path_glob)"""
    best = (0, "", "", "")
    tried = 0
    for glob in REQ_GLOBS:
        tried += 1
        cp = cache_path(host + glob)
        if os.path.exists(cp):
            c = json.load(open(cp))
            n, kinds, sample = c["n"], c["kinds"], c["sample"]
        else:
            n, kinds, sample = probe_path(host, glob)
            json.dump({"n": n, "kinds": kinds, "sample": sample}, open(cp, "w"))
            time.sleep(0.3)
        if n > best[0]:
            kind = max(kinds, key=lambda k: kinds[k]) if kinds else ""
            # The date-path form is a legacy duplicate of the same Phenom requisition.
            if kind == "phenom_datepath":
                kind = "phenom"
            best = (n, kind, sample, glob)
        if n >= 150:        # decisive: this host's req path is found
            break
        # Sites use one req path, not several. Once something is working, two more probes is
        # plenty -- trying all seven on every candidate host turned a 40-minute job into hours.
        if best[0] and tried >= 4:
            break
    return best

def cache_path(host):
    return os.path.join(CACHE, re.sub(r"[^a-z0-9.]+", "_", host.lower()) + ".json")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account")
    ap.add_argument("--refresh-stale", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    accounts = aiq.load("accounts.csv")
    active = [x for x in accounts if x["active"] == "true" and x["in_universe"] == "true"]
    existing = {r["account_id"]: r for r in aiq.load("careers.csv")}

    if a.report:
        ok = [r for r in existing.values() if r["probe_status"] == "ok"]
        need = [r for r in existing.values() if r["probe_status"] != "ok"]
        print("careers hosts resolved: %d / %d active accounts" % (len(ok), len(active)))
        by_kind = {}
        for r in ok:
            by_kind[r["pattern_kind"]] = by_kind.get(r["pattern_kind"], 0) + 1
        print("  by pattern:", by_kind)
        print("  city parseable from URL: %d" % sum(1 for r in ok if r["city_from_url"] == "true"))
        if need:
            print("  NEEDS DISCOVERY (%d): %s" % (len(need), ", ".join(sorted(r["account_id"] for r in need))))
        return

    todo = []
    for acc in active:
        aid = acc["account_id"]
        if a.account and aid != a.account:
            continue
        cur = existing.get(aid)
        if cur and not a.refresh_stale and cur.get("probe_status") == "ok":
            continue
        if cur and a.refresh_stale:
            try:
                age = (time.time() - time.mktime(time.strptime(cur["probed_at"], "%Y-%m-%d"))) / 86400
                if age < STALE_DAYS and cur["probe_status"] == "ok":
                    continue
            except ValueError:
                pass
        todo.append(acc)

    print("probing %d account(s)" % len(todo))
    for acc in todo:
        aid, company = acc["account_id"], acc["company"]
        base = re.sub(r"[^a-z0-9]", "", aiq.afold(company).lower())
        cands = SEED.get(aid, []) + ["careers.%s.com" % base, "jobs.%s.com" % base, "%s.com" % base]
        seen, best = [], None
        print("  %-24s" % aid, end="", flush=True)
        for host in cands:
            if host in seen:
                continue
            seen.append(host)
            n, kind, sample, glob = probe_host(host)
            if n and (best is None or n > best[0]):
                best = (n, host, kind, sample, glob)
            if n >= 40:              # right host found; stop trying further candidates
                break
        if best:
            n, host, kind, sample, glob = best
            row = {"account_id": aid, "company": company, "careers_host": host,
                   "pattern_kind": kind, "req_path_glob": glob, "url_sample": sample,
                   "req_urls_found": str(n),
                   "city_from_url": "true" if kind in ("phenom", "workday") else "false",
                   "probe_status": "ok", "candidates_tried": "|".join(seen),
                   "probed_at": aiq.today(), "notes": ""}
            print(" -> %s%s  %s  (%d in sample)" % (host, glob, kind, n))
        else:
            row = {"account_id": aid, "company": company, "careers_host": "", "pattern_kind": "",
                   "req_path_glob": "", "url_sample": "", "req_urls_found": "0",
                   "city_from_url": "false",
                   "probe_status": "needs_discovery", "candidates_tried": "|".join(seen),
                   "probed_at": aiq.today(),
                   "notes": "no requisition-shaped URLs in the Wayback index for any candidate host"}
            print(" -> NEEDS DISCOVERY")
        existing[aid] = row

    order = [x["account_id"] for x in active]
    rows = [existing[k] for k in order if k in existing]
    aiq.write("careers.csv", rows, FIELDS)
    ok = sum(1 for r in rows if r["probe_status"] == "ok")
    print("\nwrote data/careers.csv: %d rows, %d resolved, %d need discovery"
          % (len(rows), ok, len(rows) - ok))

if __name__ == "__main__":
    main()

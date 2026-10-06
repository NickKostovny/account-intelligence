#!/usr/bin/env python3
"""
Tier 1: build the job-posting corpus from the Wayback CDX index. Deterministic, no LLM.

What Tier 1 knows, from the requisition URL alone: the req id, the city, the role title, the
capture window, and therefore the job family, the seniority, and the site. What it does NOT know:
team names, parent orgs, tech stack, or any verbatim line -- those need a body fetch (Tier 2).
Keeping the split means the breadth layer carries zero fabrication risk, because nothing in it is
inferred by a model.

ACCRETION IS THE POINT. first_seen only ever moves earlier, last_seen only later, seen_count
increments once per run. Wayback backfills a skeleton of the past; our own uniform sampling from
here on is the only unbiased time series we will ever have. Re-running never loses history.

  python3 jobs_cdx.py --all                  # sweep every resolved account
  python3 jobs_cdx.py --account astrazeneca  # one account
  python3 jobs_cdx.py --resolve-only         # re-derive site_id from the current sites.csv
  python3 jobs_cdx.py --rollup               # recompute share-of-mix + peer baselines
  python3 jobs_cdx.py --stats                # print the corpus summary, no network

Writes data/postings.csv and data/posting_rollup.csv.
"""
import argparse, json, os, re, statistics, time
import urllib.error, urllib.parse, urllib.request

import aiq
from ats_probe import cdx, parse_req_url

FROM, TO = "20240101", time.strftime("%Y%m%d")
CDX_CACHE = os.path.join(aiq.CACHE, "cdx")
CACHE_TTL_DAYS = 14

FIELDS = [
    "posting_id", "account_id",
    "mirror_account_id", "mirror_site_id", "mirror_basis",
    "site_id", "site_resolution", "site_candidates", "city_slug", "city_raw", "also_at", "icp_fit",
    "role_title", "role_title_source", "role_slug", "req_id", "url_form", "careers_host",
    "job_family", "job_family_rule", "seniority_rank", "seniority_rule", "excluded",
    "tier2_admit", "tier2_score",
    "url", "first_seen", "first_seen_kind", "last_seen", "last_seen_kind", "seen_count",
    "period_first", "period_last",
    "posted_date", "posted_date_source",
    "extraction_tier", "body_chars", "extract_status",
    "captured_at", "updated_at", "last_run_id",
]

ROLLUP_FIELDS = ["rollup_id", "scope", "account_id", "site_id", "period", "dim_kind", "dim_key",
                 "n", "denom", "share", "account_alltime_share", "self_index",
                 "peer_median_share", "peer_n_accounts", "peer_index",
                 "bias_flag", "bias_note", "generated_at"]

MIN_DENOM = 25          # below this a quarter's share is noise
BURST_RATIO = 2.5       # denom this many times the account's median quarter = a crawl burst


def titleise(role_slug):
    if not role_slug:
        return ""
    words = [w for w in role_slug.split("-") if w]
    keep = {"it", "ai", "ml", "msat", "cmc", "qc", "pat", "mes", "lims", "eln", "pt", "d", "r",
            "usp", "dsp", "gmp", "api", "hplc", "ngs", "car", "t", "aav", "us", "uk", "emea"}
    out = []
    for w in words:
        out.append(w.upper() if w in keep else (w[:1].upper() + w[1:]))
    return " ".join(out)


def sweep_account(host, pattern_kind, req_glob="/job/*", refresh=False):
    """
    -> list of (url, timestamp). Cached; the archive is slow and this is re-run often.

    Anchored on the requisition path glob discovered by ats_probe rather than `<host>/*`, which
    would pull in every category, search and marketing page on the careers site.
    """
    os.makedirs(CDX_CACHE, exist_ok=True)
    tag = re.sub(r"[^a-z0-9.]+", "_", (host + req_glob).lower())
    cp = os.path.join(CDX_CACHE, "%s_%s_%s.json" % (tag, FROM, TO))
    if os.path.exists(cp) and not refresh:
        age = (time.time() - os.path.getmtime(cp)) / 86400
        if age < CACHE_TTL_DAYS:
            return json.load(open(cp))
    rows = cdx({"url": host + (req_glob or "/job/*"), "output": "json", "from": FROM, "to": TO,
                "collapse": "urlkey", "fl": "original,timestamp", "limit": "60000"})
    out = [[r[0], r[1]] for r in rows[1:] if r and len(r) >= 2 and str(r[0]).startswith("http")] if rows else []
    json.dump(out, open(cp, "w"))
    return out


def ingest(accounts_filter=None, refresh=False):
    careers = {r["account_id"]: r for r in aiq.load("careers.csv")}
    if not careers:
        raise SystemExit("data/careers.csv is empty -- run ats_probe.py first.")
    sites = aiq.load("sites.csv")
    idx = aiq.build_site_index(sites)
    icp_by_site = {s["site_id"]: (s.get("icp_fit") or "unknown") for s in sites}
    ov = aiq.load_overrides()
    picks = ov.get("site_picks", {})

    lk = aiq.lock(); lk.__enter__()          # held across the whole read-modify-write
    existing = {r["posting_id"]: r for r in aiq.load("postings.csv")}
    run_id = aiq.new_run_id("cdx")
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    seen_urls = new_urls = 0
    per_account = {}

    targets = [a for a in careers.values() if a["probe_status"] == "ok"
               and (not accounts_filter or a["account_id"] in accounts_filter)]
    print("sweeping %d account(s), run_id=%s" % (len(targets), run_id))

    for c in targets:
        aid, host, kind = c["account_id"], c["careers_host"], c["pattern_kind"]
        rows = sweep_account(host, kind, c.get("req_path_glob") or "/job/*", refresh=refresh)
        # Rows carrying a real req id go first, so a legacy date-path duplicate can be folded into
        # the canonical row instead of minting a second posting for the same requisition.
        parsed = []
        for url, ts in rows:
            got = parse_req_url(url, kind)
            if got:
                parsed.append((got, url, ts))
        parsed.sort(key=lambda x: 0 if x[0]["req_id"] else 1)
        by_role = {}
        n_new = n_seen = 0
        for got, url, ts in parsed:
            n_seen += 1
            role_key = (got["city_slug"], got["role_slug"])
            if got["req_id"]:
                pid = aiq.posting_id(aid, got["req_id"], got["city_slug"], got["role_slug"])
                by_role.setdefault(role_key, pid)
            else:
                # date-path form: fold into the canonical req if we have one
                pid = by_role.get(role_key) or aiq.posting_id(aid, "", got["city_slug"], got["role_slug"])
            day = ts[:4] + "-" + ts[4:6] + "-" + ts[6:8]
            row = existing.get(pid)
            if row is None:
                sid, res, cand = aiq.resolve_site(idx, aid, got["city_slug"], picks)
                mac, msid, mbasis = aiq.mirror_for(idx, aid, got["city_slug"])
                icp = icp_by_site.get(sid, "unknown")
                fam, frule, rank, srule, ex = aiq.classify(got["role_slug"])
                row = {
                    "posting_id": pid, "account_id": aid,
                    "mirror_account_id": mac, "mirror_site_id": msid, "mirror_basis": mbasis,
                    "site_id": sid, "site_resolution": res, "site_candidates": "|".join(cand),
                    "city_slug": got["city_slug"], "city_raw": got["city_raw"], "icp_fit": icp,
                    "role_title": titleise(got["role_slug"]), "role_title_source": "url_slug",
                    "role_slug": got["role_slug"], "req_id": got["req_id"],
                    "url_form": got["url_form"], "careers_host": host,
                    "job_family": fam, "job_family_rule": frule,
                    "seniority_rank": str(rank), "seniority_rule": srule, "excluded": ex,
                    "tier2_admit": "", "tier2_score": "",
                    "url": url,
                    "first_seen": day, "first_seen_kind": "cdx_capture",
                    "last_seen": day, "last_seen_kind": "cdx_capture", "seen_count": "1",
                    "period_first": aiq.quarter(day), "period_last": aiq.quarter(day),
                    "posted_date": "", "posted_date_source": "",
                    "extraction_tier": "tier0_index", "body_chars": "", "extract_status": "",
                    "captured_at": aiq.today(), "updated_at": aiq.today(), "last_run_id": run_id,
                }
                existing[pid] = row
                n_new += 1
            else:
                # accrete: the window only ever widens
                if day < row["first_seen"]:
                    row["first_seen"], row["period_first"] = day, aiq.quarter(day)
                if day > row["last_seen"]:
                    row["last_seen"], row["period_last"] = day, aiq.quarter(day)
                if row.get("last_run_id") != run_id:
                    row["seen_count"] = str(int(row.get("seen_count") or 0) + 1)
                    row["last_run_id"] = run_id
                row["updated_at"] = aiq.today()
        per_account[aid] = (n_seen, n_new)
        seen_urls += n_seen
        new_urls += n_new
        print("  %-24s %6d req urls  %5d new" % (aid, n_seen, n_new))

    rows = _rescore(list(existing.values()), idx, icp_by_site, picks)
    aiq.write("postings.csv", rows, FIELDS)
    aiq.record_unmatched(rows)
    lk.__exit__(None, None, None)
    aiq.log_run({"run_id": run_id, "kind": "cdx", "started_at": started,
                 "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "accounts": str(len(targets)), "urls_seen": str(seen_urls),
                 "new_urls": str(new_urls), "extracted": "", "quarantined": "",
                 "verify_pass_rate": "", "notes": "tier1 cdx sweep"})
    print("\npostings.csv: %d rows total (%d new this run)" % (len(rows), new_urls))
    return rows


def _rescore(rows, idx, icp_by_site, picks):
    """
    Re-derive everything that depends on sites.csv or the classifier. Runs on every ingest and via
    --resolve-only, because normalize.py reassigns site_id from source-CSV row order: a persisted
    site_id silently goes stale the moment a row is added to the site spine.
    """
    now = time.time()
    for r in rows:
        sid, res, cand = aiq.resolve_site(idx, r["account_id"], r["city_slug"], picks)
        r["site_id"], r["site_resolution"], r["site_candidates"] = sid, res, "|".join(cand)
        mac, msid, mbasis = aiq.mirror_for(idx, r["account_id"], r["city_slug"])
        r["mirror_account_id"], r["mirror_site_id"], r["mirror_basis"] = mac, msid, mbasis
        r["icp_fit"] = icp_by_site.get(sid, "unknown")
        fam, frule, rank, srule, ex = aiq.classify(r["role_slug"])
        r["job_family"], r["job_family_rule"] = fam, frule
        r["seniority_rank"], r["seniority_rule"], r["excluded"] = str(rank), srule, ex
        ad = aiq.admit(fam, rank, ex, r["icp_fit"], r["role_slug"])
        r["tier2_admit"] = "true" if ad else "false"
        try:
            age_days = (now - time.mktime(time.strptime(r["last_seen"], "%Y-%m-%d"))) / 86400
        except ValueError:
            age_days = 9999
        r["tier2_score"] = str(aiq.score(fam, rank, r["icp_fit"], age_days <= 365,
                                         int(r.get("seen_count") or 1)))
    return rows


def rollup():
    """
    Share-of-mix, not counts.

    The CDX timestamp is a FIRST-CAPTURE date, not a posted date, so raw per-quarter counts track
    how hard the archive crawled that quarter. AstraZeneca's 2024Q2 shows ~304 data/analytics reqs
    against 12-41 in every other quarter: that is a crawl burst, not a hiring burst. Three
    normalisations, weakest to strongest:

      share        n / that account's total captured reqs in the period. "Concentration of job
                   types" is inherently a share, which is what was actually asked for.
      self_index   share / that account's all-time share for the family. Cancels account-level
                   crawl volume.
      peer_index   share / the median share across accounts in the SAME quarter. This is the real
                   fix: anything common to the crawl regime that quarter divides out, because every
                   account was crawled under the same regime.

    bias_flag + a precomputed bias_note travel with every row so the renderer cannot draw a chart
    without the caveat available.
    """
    rows = aiq.load("postings.csv")
    if not rows:
        raise SystemExit("postings.csv is empty -- run the sweep first.")
    gen = aiq.today()
    out = []

    # denominators: reqs captured per (account, period) and per account all-time
    denom_ap, denom_a = {}, {}
    fam_ap, fam_a = {}, {}
    for r in rows:
        aid, per, fam = r["account_id"], r["period_first"], r["job_family"] or "unclassified"
        if not per:
            continue
        denom_ap[(aid, per)] = denom_ap.get((aid, per), 0) + 1
        denom_a[aid] = denom_a.get(aid, 0) + 1
        fam_ap[(aid, per, fam)] = fam_ap.get((aid, per, fam), 0) + 1
        fam_a[(aid, fam)] = fam_a.get((aid, fam), 0) + 1

    # a per-account median quarter, for burst detection
    med_denom = {}
    for aid in denom_a:
        vals = [v for (a, p), v in denom_ap.items() if a == aid and v > 0]
        med_denom[aid] = statistics.median(vals) if vals else 0

    # peer median share per (period, family), restricted to accounts with a usable denominator
    peer = {}
    for (aid, per, fam), n in fam_ap.items():
        d = denom_ap[(aid, per)]
        if d >= MIN_DENOM:
            peer.setdefault((per, fam), []).append(n / float(d))

    for (aid, per, fam), n in sorted(fam_ap.items()):
        d = denom_ap[(aid, per)]
        share = n / float(d) if d else 0.0
        alltime = (fam_a[(aid, fam)] / float(denom_a[aid])) if denom_a.get(aid) else 0.0
        self_i = (share / alltime) if alltime else 0.0
        shares = peer.get((per, fam), [])
        pmed = statistics.median(shares) if shares else 0.0
        peer_i = (share / pmed) if pmed else 0.0
        flag, note = "", ""
        if d < MIN_DENOM:
            flag = "low_n"
            note = ("Only %d reqs captured for this account in %s; a share off that base is noise. "
                    "Counts are not comparable across quarters." % (d, per))
        elif med_denom.get(aid) and d > BURST_RATIO * med_denom[aid]:
            flag = "crawl_burst"
            note = ("Denominator %d reqs is %.1fx this account's median quarter (%d) -- a Wayback "
                    "crawl burst, not a hiring burst. Share is reported; counts are not comparable "
                    "across quarters." % (d, d / float(med_denom[aid]), med_denom[aid]))
        out.append({
            "rollup_id": "account_period_family~%s~~%s~job_family~%s" % (aid, per, fam),
            "scope": "account_period_family", "account_id": aid, "site_id": "", "period": per,
            "dim_kind": "job_family", "dim_key": fam,
            "n": str(n), "denom": str(d), "share": "%.4f" % share,
            "account_alltime_share": "%.4f" % alltime, "self_index": "%.3f" % self_i,
            "peer_median_share": "%.4f" % pmed, "peer_n_accounts": str(len(shares)),
            "peer_index": "%.3f" % peer_i, "bias_flag": flag, "bias_note": note,
            "generated_at": gen})

    # site power concentration: share of an account's ORG_CORE + senior reqs landing at each site
    core_site, core_acct = {}, {}
    for r in rows:
        if r["site_id"] and (r["job_family"] in aiq.ORG_CORE or int(r["seniority_rank"] or 1) >= 4):
            core_site[(r["account_id"], r["site_id"])] = core_site.get((r["account_id"], r["site_id"]), 0) + 1
            core_acct[r["account_id"]] = core_acct.get(r["account_id"], 0) + 1
    for (aid, sid), n in sorted(core_site.items()):
        d = core_acct[aid]
        out.append({
            "rollup_id": "site_alltime_power~%s~%s~alltime~power~core_senior" % (aid, sid),
            "scope": "site_alltime_power", "account_id": aid, "site_id": sid, "period": "alltime",
            "dim_kind": "power", "dim_key": "core_senior",
            "n": str(n), "denom": str(d), "share": "%.4f" % (n / float(d)),
            "account_alltime_share": "", "self_index": "", "peer_median_share": "",
            "peer_n_accounts": "", "peer_index": "",
            "bias_flag": "low_n" if d < MIN_DENOM else "",
            "bias_note": ("Only %d core/senior reqs for this account in total; site ranking is "
                          "indicative, not a measurement." % d) if d < MIN_DENOM else "",
            "generated_at": gen})

    aiq.write("posting_rollup.csv", out, ROLLUP_FIELDS)
    nb = sum(1 for r in out if r["bias_flag"])
    print("posting_rollup.csv: %d rows (%d carry a bias flag + note)" % (len(out), nb))
    return out


def stats():
    rows = aiq.load("postings.csv")
    if not rows:
        print("postings.csv is empty."); return
    from collections import Counter
    print("postings: %d" % len(rows))
    print("  accounts:            %d" % len({r['account_id'] for r in rows}))
    print("  site_resolution:    ", dict(Counter(r["site_resolution"] for r in rows)))
    print("  job_family:         ", dict(Counter(r["job_family"] or "(none)" for r in rows).most_common(9)))
    print("  tier2 admitted:      %d" % sum(1 for r in rows if r["tier2_admit"] == "true"))
    print("  mirrored:           ", dict(Counter(r["mirror_basis"] for r in rows if r["mirror_basis"])))
    print("  extraction_tier:    ", dict(Counter(r["extraction_tier"] for r in rows)))
    orphan = [r for r in rows if r["site_id"] and r["site_id"] not in
              {s["site_id"] for s in aiq.load("sites.csv")}]
    print("  FK violations:       %d (must be 0)" % len(orphan))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--account", action="append")
    ap.add_argument("--refresh", action="store_true", help="ignore the CDX disk cache")
    ap.add_argument("--resolve-only", action="store_true")
    ap.add_argument("--rollup", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    if a.stats:
        stats(); return
    if a.resolve_only:
        sites = aiq.load("sites.csv")
        idx = aiq.build_site_index(sites)
        icp = {s["site_id"]: (s.get("icp_fit") or "unknown") for s in sites}
        rows = _rescore(aiq.load("postings.csv"), idx, icp, aiq.load_overrides().get("site_picks", {}))
        aiq.write("postings.csv", rows, FIELDS)
        um = aiq.record_unmatched(rows)
        print("re-resolved site_id / family / score for %d postings" % len(rows))
        print("unmatched_cities.csv: %d location string(s) resolved to no site" % len(um))
        return
    if a.all or a.account:
        ingest(set(a.account) if a.account else None, refresh=a.refresh)
        rollup()
        stats()
        return
    if a.rollup:
        rollup(); return
    ap.print_help()


if __name__ == "__main__":
    main()

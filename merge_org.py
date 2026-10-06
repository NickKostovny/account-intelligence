#!/usr/bin/env python3
"""
Fold a Tier-2 extraction result into the SSOT, applying the verbatim gate as it goes.

Two tables, two different write disciplines:

  posting_extracts.csv  upsert on posting_id, TIER-MONOTONIC. A cheap archive re-extraction can
                        never overwrite a richer live-body one. Without this, a refresh that
                        happens to find only a stale snapshot silently degrades good data.
  posting_quotes.csv    append-only join table. Provenance is many-to-many with a payload: one
                        posting yields several quotes, one org edge rests on quotes from several
                        postings. Pipe-joining quotes into an edge cell would destroy the
                        quote-to-posting-to-date pairing, which is the whole requirement.

A claim whose supporting quote fails verification is DROPPED from the extract and recorded in
quarantine_org.csv. The claim does not survive in a weaker form.

  python3 merge_org.py ../org_out_20260729.json
"""
import argparse, html as _html, json, os, re, sys

import aiq
from jobs_cdx import FIELDS as POSTING_FIELDS
from verify_org import audit, verify_quote, load_bodies

EX_FIELDS = [
    "posting_id", "account_id", "site_id_at_extract",
    "team_name", "team_slug", "team_acronym", "team_name_conf",
    "parent_org", "parent_slug", "parent_conf",
    "supports_teams", "supports_slugs",
    "reports_to_title", "hiring_manager", "hiring_manager_conf",
    "site_stated", "posted_date_verbatim",
    "tech_stack", "tech_stack_raw",
    "vocabulary", "autonomy_flag", "acquisition_flag",
    "n_quotes", "n_quotes_verified", "n_claims_dropped",
    "body_chars", "extraction_tier", "extract_run_id", "extract_status", "extract_notes",
    "captured_at",
]
QUOTE_FIELDS = [
    "quote_id", "posting_id", "account_id", "claim_type", "subject", "subject_slug",
    "quote_text", "verbatim_verified", "match_method",
    "display_date", "date_kind", "extraction_tier", "extract_run_id", "captured_at",
]

# Team names arrive with cosmetic variation ("Operations IT - Global Biologics" vs "Operations IT
# Global Biologics" vs "the Operations IT Global Biologics team"). Collapse to a comparison slug so
# they land on one org unit, without merging genuinely different teams.
_TRAILING = re.compile(r"\b(team|teams|group|groups|department|departments|dept|organisation|"
                       r"organization|org|function|functions|unit|coe|centre of excellence|"
                       r"center of excellence)\b\s*$", re.I)

def team_slug(name):
    s = aiq.afold(name or "").lower()
    s = re.sub(r"^\s*(the|our|a)\s+", "", s)
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for _ in range(2):
        s = _TRAILING.sub("", s).strip()
    s = re.sub(r"\band\b", "&", s)
    return re.sub(r"\s+", "-", s).strip("-")


# Corporate boilerplate that is not a parent org. Stage 0 caught the extractor offering "part of
# the AstraZeneca group" as a parent -- that is marketing copy about the whole company.
def is_boilerplate_parent(parent, company_names):
    p = team_slug(parent)
    if not p or len(p) < 3:
        return True
    if p in {"group", "company", "business", "organisation", "organization", "global"}:
        return True
    for c in company_names:
        cs = team_slug(c)
        if cs and (p == cs or p in (cs + "-group", cs + "-plc", cs + "-inc")):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--run-id", default="")
    a = ap.parse_args()

    data = json.load(open(a.path, encoding="utf-8"))
    if isinstance(data, dict) and "postings" not in data:
        data = data.get("result", data)
    postings = data.get("postings", [])
    if not postings:
        sys.exit("no postings in %s" % a.path)

    # Source pages store "&" but their HTML stores "&amp;", and the extractor faithfully copies
    # whichever it saw. Unescape once on ingest, or "Oncology R&amp;D" gets escaped a second time at
    # render and shows up as "R&amp;amp;D" in the brief.
    def unesc(x):
        if isinstance(x, str):
            return _html.unescape(x)
        if isinstance(x, list):
            return [unesc(i) for i in x]
        if isinstance(x, dict):
            return {k: unesc(v) for k, v in x.items()}
        return x
    postings = unesc(postings)

    run_id = a.run_id or aiq.new_run_id("org")
    bodies = load_bodies(postings)

    prows = aiq.load("postings.csv")
    pby = {r["posting_id"]: r for r in prows}
    accounts = aiq.load("accounts.csv")
    company_by_aid = {x["account_id"]: x["company"] for x in accounts}
    all_companies = [x["company"] for x in accounts]

    ex_rows = aiq.load("posting_extracts.csv")
    ex_by = {r["posting_id"]: r for r in ex_rows}
    q_rows = aiq.load("posting_quotes.csv")
    q_seen = {(r["posting_id"], r["claim_type"], r["subject_slug"], r["quote_text"][:80])
              for r in q_rows}

    n_up = n_skip_tier = n_drop = n_qadd = 0
    stats = {"verified": 0, "failed": 0}

    for p in postings:
        pid = p.get("posting_id") or ""
        src = pby.get(pid)
        if not src:
            print("  ! unknown posting_id %s -- skipped (not in postings.csv)" % pid)
            continue
        aid = src["account_id"]
        body = bodies.get(pid, "")
        tier = src.get("extraction_tier") or "tier2_archive_body"

        # Verify every quote once, then index the verdict by (claim_type, subject_slug) so each
        # structured field can be checked for support.
        support = {}
        for q in p.get("quotes") or []:
            ok, method = verify_quote(body, q.get("quote_text", ""))
            stats["verified" if ok else "failed"] += 1
            key = (q.get("claim_type", ""), team_slug(q.get("subject", "")))
            if ok:
                support.setdefault(key, []).append((q, method))
            qid = "%s--q%02d" % (pid, len(q_rows) + 1)
            sig = (pid, q.get("claim_type", ""), team_slug(q.get("subject", "")),
                   (q.get("quote_text", "") or "")[:80])
            if sig in q_seen:
                continue
            q_seen.add(sig)
            q_rows.append({
                "quote_id": qid, "posting_id": pid, "account_id": aid,
                "claim_type": q.get("claim_type", ""), "subject": q.get("subject", ""),
                "subject_slug": team_slug(q.get("subject", "")),
                "quote_text": q.get("quote_text", ""),
                "verbatim_verified": "true" if ok else "false", "match_method": method,
                "display_date": src.get("posted_date") or src.get("first_seen", ""),
                "date_kind": ("posted_date" if src.get("posted_date") else "cdx_capture"),
                "extraction_tier": tier, "extract_run_id": run_id, "captured_at": aiq.today(),
            })
            n_qadd += 1

        # Verified quotes of each claim type, for the containment fallback below.
        by_type = {}
        for (ct, _slug), lst in support.items():
            by_type.setdefault(ct, []).extend(lst)

        def backed(claim_type, subject):
            """
            A field survives only if a VERIFIED quote of the right claim type supports it.

            Exact subject match first. Then a containment fallback, because an extractor legitimately
            groups several teams under one line -- "...to FP&A and preferably other Corporate
            Functions, such as Procurement, HR, Legal, Compliance" is one quote covering four
            supports_teams entries, each with its own combined subject string. Requiring an exact
            subject_slug match dropped 96 such claims for Biogen even though every quote had passed
            the verbatim gate.

            This does NOT weaken the guarantee: the quote must still have been verified as a literal
            substring of the body, AND it must actually name the subject. What relaxes is only the
            bookkeeping link between a claim and which quote carries it.
            """
            if support.get((claim_type, team_slug(subject))):
                return True
            subj = _html.unescape(subject or "").strip().lower()
            if len(subj) < 3:
                return False
            for q, _m in by_type.get(claim_type, []):
                if subj in _html.unescape(q.get("quote_text", "") or "").lower():
                    return True
            return False

        dropped = []
        team = (p.get("team_name") or "").strip()
        if team and not backed("team_name", team):
            dropped.append("team_name"); team = ""
        parent = (p.get("parent_org") or "").strip()
        if parent and not backed("parent_org", parent):
            dropped.append("parent_org"); parent = ""
        if parent and is_boilerplate_parent(parent, all_companies + [company_by_aid.get(aid, "")]):
            dropped.append("parent_org(boilerplate)"); parent = ""
        acro = (p.get("team_acronym") or "").strip()
        if acro and not (backed("team_acronym", acro) or backed("team_name", team)):
            dropped.append("team_acronym"); acro = ""
        hm = (p.get("hiring_manager") or "").strip()
        if hm and not backed("hiring_manager", hm):
            dropped.append("hiring_manager"); hm = ""
        rtt = (p.get("reports_to_title") or "").strip()
        if rtt and not backed("reports_to", rtt):
            dropped.append("reports_to_title"); rtt = ""
        site_st = (p.get("site_stated") or "").strip()
        if site_st and not backed("site_stated", site_st):
            dropped.append("site_stated"); site_st = ""

        sup = [s for s in (p.get("supports_teams") or [])
               if s.get("team") and backed("supports_team", s["team"])]
        dropped += ["supports:%s" % s["team"] for s in (p.get("supports_teams") or [])
                    if s.get("team") and not backed("supports_team", s["team"])]
        tech = [t for t in (p.get("tech_stack") or []) if t.get("tool") and backed("tech", t["tool"])]
        dropped += ["tech:%s" % t["tool"] for t in (p.get("tech_stack") or [])
                    if t.get("tool") and not backed("tech", t["tool"])]
        vocab = [v for v in (p.get("vocabulary") or []) if verify_quote(body, v)[0]]
        n_drop += len(dropped)

        row = {
            "posting_id": pid, "account_id": aid, "site_id_at_extract": src.get("site_id", ""),
            "team_name": team, "team_slug": team_slug(team), "team_acronym": acro,
            "team_name_conf": "high" if team else "",
            "parent_org": parent, "parent_slug": team_slug(parent),
            "parent_conf": "high" if parent else "",
            "supports_teams": "|".join(s["team"] for s in sup),
            "supports_slugs": "|".join(team_slug(s["team"]) for s in sup),
            "reports_to_title": rtt, "hiring_manager": hm,
            "hiring_manager_conf": "high" if hm else "",
            "site_stated": site_st, "posted_date_verbatim": (p.get("posted_date") or ""),
            "tech_stack": "|".join(t["tool"] for t in tech),
            "tech_stack_raw": "|".join((t.get("tool") or "") for t in (p.get("tech_stack") or [])),
            "vocabulary": "|".join(vocab),
            "autonomy_flag": (p.get("autonomy_flag") or ""),
            "acquisition_flag": (p.get("acquisition_flag") or ""),
            "n_quotes": str(len(p.get("quotes") or [])),
            "n_quotes_verified": str(sum(len(v) for v in support.values())),
            "n_claims_dropped": str(len(dropped)),
            "body_chars": src.get("body_chars", ""), "extraction_tier": tier,
            "extract_run_id": run_id,
            "extract_status": "ok" if team or parent or tech or sup else "thin",
            "extract_notes": ((p.get("notes") or "")[:300]
                              + ((" | DROPPED unsupported: " + ", ".join(dropped[:8])) if dropped else "")),
            "captured_at": aiq.today(),
        }
        old = ex_by.get(pid)
        if old and aiq.tier_rank(tier) < aiq.tier_rank(old.get("extraction_tier") or ""):
            n_skip_tier += 1
        else:
            ex_by[pid] = row
            n_up += 1
        if src.get("posted_date_verbatim") is None:
            pass
        if p.get("posted_date") and not src.get("posted_date"):
            src["posted_date"] = p["posted_date"]
            src["posted_date_source"] = "body_posted_date"
        src["extract_status"] = row["extract_status"]

    aiq.write("posting_extracts.csv", list(ex_by.values()), EX_FIELDS)
    aiq.write("posting_quotes.csv", q_rows, QUOTE_FIELDS)
    aiq.write("postings.csv", prows, POSTING_FIELDS)

    tot = stats["verified"] + stats["failed"]
    rate = (100.0 * stats["verified"] / tot) if tot else 0.0
    audit(a.path, write=True)     # record the failures in quarantine_org.csv
    aiq.log_run({"run_id": run_id, "kind": "org_merge",
                 "started_at": "", "finished_at": aiq.today(),
                 "accounts": str(len({r['account_id'] for r in ex_by.values()})),
                 "urls_seen": "", "new_urls": "", "extracted": str(n_up),
                 "quarantined": str(stats["failed"]), "verify_pass_rate": "%.1f" % rate,
                 "notes": "merge_org from %s" % os.path.basename(a.path)})

    print("=== merge_org ===")
    print("extracts upserted     : %d  (%d skipped: a richer tier already on file)" % (n_up, n_skip_tier))
    print("quotes appended       : %d" % n_qadd)
    print("quote verification    : %d/%d verified (%.1f%%)" % (stats["verified"], tot, rate))
    print("unsupported claims dropped: %d" % n_drop)
    print("posting_extracts.csv  : %d rows" % len(ex_by))
    print("posting_quotes.csv    : %d rows" % len(q_rows))


if __name__ == "__main__":
    main()

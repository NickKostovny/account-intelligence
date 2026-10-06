#!/usr/bin/env python3
"""
Assemble org units and org edges from the verified extracts. Full rewrite each run -- these are
derived views over posting_extracts.csv + posting_quotes.csv, never hand-edited. Human judgement
lives in org_overrides.json and is applied on top.

Three rules the whole design rests on:

  1. NO EDGE WITHOUT A VERIFIED QUOTE. An inferred reporting line with nothing quotable behind it
     does not get written, and the writer asserts this before saving. That single rule is what
     makes the map interrogable instead of an opinion with boxes drawn round it.
  2. A PARENT NAMED BUT NOT FOUND BECOMES A GHOST UNIT, not a dropped edge. "reports into Global
     Biologics Operations IT" is real evidence even when no such unit was reconstructed; the map
     must show the line running into something it could not resolve.
  3. GAPS ARE DATA. gap_flags and org_site_coverage.csv exist so the renderer can show absence as
     absence -- a site nobody scanned must never look like a site with no hiring.

  python3 build_org.py
  python3 build_org.py --account astrazeneca --report
"""
import argparse, os, re
from collections import defaultdict, Counter

import aiq
from merge_org import team_slug

UNIT_FIELDS = [
    "unit_id", "account_id", "unit_slug", "unit_name_display", "unit_name_generated",
    "acronym", "acronym_source", "aliases", "unit_kind",
    "site_id", "site_resolution", "site_candidates",
    "parent_unit_id", "parent_label", "parent_conf",
    "n_postings", "n_postings_tier3plus", "n_quotes",
    "first_evidence_date", "last_evidence_date", "evidence_date_kind",
    "job_families", "seniority_max", "tech_stack", "vocabulary", "hiring_managers",
    "evidence_posting_ids", "evidence_quote_ids",
    "status", "confidence", "confidence_basis", "gap_flags",
    "human_state", "human_note", "generated_at",
]
EDGE_FIELDS = [
    "edge_id", "account_id", "edge_type", "src_unit_id", "dst_unit_id", "dst_site_id",
    "src_label", "dst_label",
    "n_postings", "n_postings_tier3plus", "n_quotes", "n_independent_postings",
    "first_evidence_date", "last_evidence_date", "evidence_date_kind",
    "evidence_posting_ids", "evidence_quote_ids",
    "confidence", "confidence_basis", "status", "human_state", "human_note", "generated_at",
]
COV_FIELDS = [
    "account_id", "site_id", "site_name", "city", "icp_fit",
    "n_indexed", "n_admitted", "n_bodies", "n_extracts", "n_units",
    "first_seen", "last_seen", "coverage_state", "scanned_at",
]

RICH_TIERS = {"tier3_live_body", "tier4_manual"}


def conf_from(n_postings, n_tier3, n_quotes):
    """
    Confidence is DERIVED from evidence volume and independence, never asserted by a model.
    Returns (level, human-readable basis) -- the basis string is precomputed so the renderer can
    show why without re-deriving it.
    """
    if n_postings >= 3 or (n_postings >= 2 and n_tier3 >= 2):
        lvl = "high"
    elif n_postings >= 2 or n_tier3 >= 1:
        lvl = "medium"
    else:
        lvl = "low"
    # Reader-facing string. "rich-body" was internal jargon leaking into the panel; whether the
    # evidence came from archived or live postings is already surfaced as a gap flag.
    return lvl, "%d posting%s, %d verified quote%s" % (
        n_postings, "" if n_postings == 1 else "s", n_quotes, "" if n_quotes == 1 else "s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", action="append")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    gen = aiq.today()
    sites = aiq.load("sites.csv")
    site_by_id = {s["site_id"]: s for s in sites}
    postings = aiq.load("postings.csv")
    p_by_id = {r["posting_id"]: r for r in postings}
    extracts = aiq.load("posting_extracts.csv")
    quotes = aiq.load("posting_quotes.csv")
    ov = aiq.load_overrides()

    if a.account:
        keep = set(a.account)
        extracts = [e for e in extracts if e["account_id"] in keep]

    # verified quotes only, indexed for evidence lookup
    q_by = defaultdict(list)
    for q in quotes:
        if q.get("verbatim_verified") == "true":
            q_by[(q["posting_id"], q["claim_type"], q["subject_slug"])].append(q)

    def qids(pid, ctype, subject):
        return [x["quote_id"] for x in q_by.get((pid, ctype, team_slug(subject)), [])]

    # ---------------------------------------------------------------- units
    agg = defaultdict(lambda: {
        "names": Counter(), "acros": Counter(), "sites": Counter(), "fams": Counter(),
        "sen": 0, "tech": Counter(), "vocab": [], "hm": Counter(),
        "pids": [], "qids": [], "tier3": 0, "dates": [], "parents": Counter()})

    for e in extracts:
        slug = e.get("team_slug") or ""
        if not slug:
            continue
        aid = e["account_id"]
        u = agg[(aid, slug)]
        u["names"][e["team_name"]] += 1
        if e.get("team_acronym"):
            u["acros"][e["team_acronym"]] += 1
        src = p_by_id.get(e["posting_id"], {})
        if src.get("site_id"):
            u["sites"][src["site_id"]] += 1
        if src.get("job_family"):
            u["fams"][src["job_family"]] += 1
        u["sen"] = max(u["sen"], int(src.get("seniority_rank") or 1))
        for t in (e.get("tech_stack") or "").split("|"):
            if t:
                u["tech"][t] += 1
        for v in (e.get("vocabulary") or "").split("|"):
            if v and v not in u["vocab"]:
                u["vocab"].append(v)
        if e.get("hiring_manager"):
            u["hm"][e["hiring_manager"]] += 1
        u["pids"].append(e["posting_id"])
        u["qids"] += qids(e["posting_id"], "team_name", e["team_name"])
        if e.get("extraction_tier") in RICH_TIERS:
            u["tier3"] += 1
        d = src.get("posted_date") or src.get("first_seen") or ""
        if d:
            u["dates"].append(d)
        if e.get("parent_slug"):
            u["parents"][(e["parent_slug"], e["parent_org"])] += 1

    units = {}
    for (aid, slug), u in agg.items():
        uid = aiq.unit_id(aid, slug)
        name = u["names"].most_common(1)[0][0] if u["names"] else slug.replace("-", " ")
        acro = u["acros"].most_common(1)[0][0] if u["acros"] else ""
        n_p = len(set(u["pids"]))
        lvl, basis = conf_from(n_p, u["tier3"], len(set(u["qids"])))
        site_id, site_res, cands = "", "", []
        if u["sites"]:
            top = u["sites"].most_common()
            site_id = top[0][0]
            cands = [s for s, _ in top]
            site_res = "modal_of_postings" if len(top) > 1 else "single_site"
        units[uid] = {
            "unit_id": uid, "account_id": aid, "unit_slug": slug,
            "unit_name_display": name, "unit_name_generated": name,
            "acronym": acro, "acronym_source": "posting" if acro else "", "aliases": "",
            "unit_kind": "observed",
            "site_id": site_id, "site_resolution": site_res,
            "site_candidates": "|".join(cands),
            "parent_unit_id": "", "parent_label": "", "parent_conf": "",
            "n_postings": str(n_p), "n_postings_tier3plus": str(u["tier3"]),
            "n_quotes": str(len(set(u["qids"]))),
            "first_evidence_date": min(u["dates"]) if u["dates"] else "",
            "last_evidence_date": max(u["dates"]) if u["dates"] else "",
            "evidence_date_kind": "mixed",
            "job_families": "|".join(f for f, _ in u["fams"].most_common()),
            "seniority_max": str(u["sen"]),
            "tech_stack": "|".join(t for t, _ in u["tech"].most_common()),
            "vocabulary": "|".join(u["vocab"][:6]),
            "hiring_managers": "|".join(h for h, _ in u["hm"].most_common()),
            "evidence_posting_ids": "|".join(sorted(set(u["pids"]))),
            "evidence_quote_ids": "|".join(sorted(set(u["qids"]))),
            "status": "candidate", "confidence": lvl, "confidence_basis": basis,
            "gap_flags": "", "human_state": "", "human_note": "", "generated_at": gen,
        }

    # Acronym reconciliation: a unit whose whole name IS another unit's acronym is the same team
    # under a shorter label. Fold it in as an alias rather than leaving two nodes on the map.
    by_slug = {(u["account_id"], u["unit_slug"]): u for u in units.values()}
    merged = set()
    for u in list(units.values()):
        if not u["acronym"]:
            continue
        target = by_slug.get((u["account_id"], team_slug(u["acronym"])))
        if target and target["unit_id"] != u["unit_id"] and target["unit_id"] not in merged:
            u["aliases"] = "|".join(x for x in [u["aliases"], target["unit_name_display"]] if x)
            u["n_postings"] = str(int(u["n_postings"]) + int(target["n_postings"]))
            u["evidence_posting_ids"] = "|".join(sorted(set(
                (u["evidence_posting_ids"] + "|" + target["evidence_posting_ids"]).strip("|").split("|"))))
            merged.add(target["unit_id"])
    for uid in merged:
        units.pop(uid, None)

    # ---------------------------------------------------------------- edges
    edges = {}

    def add_edge(aid, etype, src_uid, dst_uid, dst_site, src_label, dst_label, pid, qs, date):
        eid = aiq.edge_id(aid, etype, src_uid, dst_uid or dst_site)
        e = edges.get(eid)
        if not e:
            e = {"edge_id": eid, "account_id": aid, "edge_type": etype,
                 "src_unit_id": src_uid, "dst_unit_id": dst_uid, "dst_site_id": dst_site,
                 "src_label": src_label, "dst_label": dst_label,
                 "n_postings": "0", "n_postings_tier3plus": "0", "n_quotes": "0",
                 "n_independent_postings": "0",
                 "first_evidence_date": date, "last_evidence_date": date,
                 "evidence_date_kind": "mixed",
                 "evidence_posting_ids": "", "evidence_quote_ids": "",
                 "confidence": "low", "confidence_basis": "", "status": "candidate",
                 "human_state": "", "human_note": "", "generated_at": gen}
            edges[eid] = e
        pids = set(x for x in e["evidence_posting_ids"].split("|") if x) | {pid}
        qids_ = set(x for x in e["evidence_quote_ids"].split("|") if x) | set(qs)
        e["evidence_posting_ids"] = "|".join(sorted(pids))
        e["evidence_quote_ids"] = "|".join(sorted(qids_))
        e["n_postings"] = str(len(pids))
        e["n_independent_postings"] = str(len(pids))
        e["n_quotes"] = str(len(qids_))
        t3 = sum(1 for p in pids
                 if (p_by_id.get(p, {}).get("extraction_tier") or "") in RICH_TIERS)
        e["n_postings_tier3plus"] = str(t3)
        if date:
            e["first_evidence_date"] = min(x for x in [e["first_evidence_date"], date] if x)
            e["last_evidence_date"] = max(x for x in [e["last_evidence_date"], date] if x)
        lvl, basis = conf_from(len(pids), t3, len(qids_))
        e["confidence"], e["confidence_basis"] = lvl, basis

    def ghost(aid, label):
        """A parent or peer named in a quote but never reconstructed as a unit of its own."""
        slug = team_slug(label)
        uid = aiq.unit_id(aid, slug)
        if uid not in units:
            units[uid] = {
                "unit_id": uid, "account_id": aid, "unit_slug": slug,
                "unit_name_display": label, "unit_name_generated": label,
                "acronym": "", "acronym_source": "", "aliases": "",
                "unit_kind": "named_not_mapped",
                "site_id": "", "site_resolution": "", "site_candidates": "",
                "parent_unit_id": "", "parent_label": "", "parent_conf": "",
                "n_postings": "0", "n_postings_tier3plus": "0", "n_quotes": "0",
                "first_evidence_date": "", "last_evidence_date": "", "evidence_date_kind": "",
                "job_families": "", "seniority_max": "", "tech_stack": "", "vocabulary": "",
                "hiring_managers": "", "evidence_posting_ids": "", "evidence_quote_ids": "",
                "status": "candidate", "confidence": "low",
                "confidence_basis": "named in a posting; no unit of its own was reconstructed",
                "gap_flags": "mention_only", "human_state": "", "human_note": "",
                "generated_at": gen}
        return uid

    for e in extracts:
        aid, slug = e["account_id"], e.get("team_slug") or ""
        if not slug:
            continue
        src_uid = aiq.unit_id(aid, slug)
        if src_uid not in units:
            continue                            # folded into an acronym alias
        src = p_by_id.get(e["posting_id"], {})
        date = src.get("posted_date") or src.get("first_seen") or ""

        if e.get("parent_slug"):
            qs = qids(e["posting_id"], "parent_org", e["parent_org"])
            if qs:                              # RULE 1: no verified quote, no edge
                dst = units.get(aiq.unit_id(aid, e["parent_slug"]))
                dst_uid = dst["unit_id"] if dst else ghost(aid, e["parent_org"])
                add_edge(aid, "reports_to", src_uid, dst_uid, "", e["team_name"],
                         e["parent_org"], e["posting_id"], qs, date)

        for label in (e.get("supports_teams") or "").split("|"):
            if not label:
                continue
            qs = qids(e["posting_id"], "supports_team", label)
            if not qs:
                continue
            dst = units.get(aiq.unit_id(aid, team_slug(label)))
            dst_uid = dst["unit_id"] if dst else ghost(aid, label)
            add_edge(aid, "supports", src_uid, dst_uid, "", e["team_name"], label,
                     e["posting_id"], qs, date)

        if src.get("site_id"):
            add_edge(aid, "located_at", src_uid, "", src["site_id"], e["team_name"],
                     site_by_id.get(src["site_id"], {}).get("site", src["site_id"]),
                     e["posting_id"], qids(e["posting_id"], "site_stated", e.get("site_stated", ""))
                     or qids(e["posting_id"], "team_name", e["team_name"]), date)

    # parent_unit_id on the unit row, for the tree layout
    for e in edges.values():
        if e["edge_type"] == "reports_to" and e["src_unit_id"] in units:
            u = units[e["src_unit_id"]]
            if not u["parent_unit_id"]:
                u["parent_unit_id"] = e["dst_unit_id"]
                u["parent_label"] = e["dst_label"]
                u["parent_conf"] = e["confidence"]

    # ---------------------------------------------------------------- human overrides
    for uid, v in (ov.get("unit_verdicts") or {}).items():
        if uid in units:
            u = units[uid]
            u["human_state"] = v.get("v", "")
            u["human_note"] = v.get("note", "")
            if v.get("v") == "confirm":
                u["status"], u["confidence"] = "promoted", "human"
                u["confidence_basis"] = "confirmed by a person" + (
                    " -- " + v["note"] if v.get("note") else "")
            elif v.get("v") == "reject":
                u["status"] = "rejected"
    for uid, name in (ov.get("unit_renames") or {}).items():
        if uid in units:
            units[uid]["unit_name_display"] = name
    for eid, v in (ov.get("edge_verdicts") or {}).items():
        if eid in edges:
            e = edges[eid]
            e["human_state"] = v.get("v", "")
            e["human_note"] = v.get("note", "")
            if v.get("v") == "confirm":
                e["status"], e["confidence"] = "promoted", "human"
                e["confidence_basis"] = "confirmed by a person" + (
                    " -- " + v["note"] if v.get("note") else "")
            elif v.get("v") == "reject":
                e["status"] = "rejected"

    # ---------------------------------------------------------------- gap flags
    for u in units.values():
        g = []
        if u["unit_kind"] == "named_not_mapped":
            g.append("mention_only")
        if not u["parent_unit_id"] and u["unit_kind"] == "observed":
            g.append("no_parent")
        if not u["site_id"]:
            g.append("no_site")
        elif len([c for c in u["site_candidates"].split("|") if c]) > 1:
            g.append("site_split")
        if u["n_postings"] == "1":
            g.append("single_posting")
        if not u["tech_stack"]:
            g.append("no_tech")
        if not u["hiring_managers"]:
            g.append("no_named_people")
        if u["acronym"] and not u["aliases"] and len(u["acronym"]) <= 5 and u["n_postings"] == "1":
            g.append("acronym_unexpanded")
        if u["n_postings_tier3plus"] == "0" and u["unit_kind"] == "observed":
            g.append("archive_only_evidence")
        u["gap_flags"] = "|".join(dict.fromkeys(g))

    # ---------------------------------------------------------------- RULE 1 assertion
    bad = [e for e in edges.values()
           if not e["evidence_quote_ids"] and e.get("human_state") != "confirm"]
    if bad:
        raise SystemExit(
            "EDGE GUARD: %d edge(s) have no verified verbatim quote and were not confirmed by a "
            "person. Refusing to write -- an unquotable inference must not render as a line on the "
            "map. First: %s" % (len(bad), bad[0]["edge_id"]))

    # ---------------------------------------------------------------- site coverage
    idx_by_site, adm_by_site, body_by_site = Counter(), Counter(), Counter()
    first_by, last_by = {}, {}
    for r in postings:
        sid = r.get("site_id")
        if not sid:
            continue
        k = (r["account_id"], sid)
        idx_by_site[k] += 1
        if r["tier2_admit"] == "true":
            adm_by_site[k] += 1
        if (r.get("extraction_tier") or "").startswith(("tier2", "tier3", "tier4")):
            body_by_site[k] += 1
        d = r.get("first_seen") or ""
        if d:
            first_by[k] = min(first_by.get(k, d), d)
            last_by[k] = max(last_by.get(k, d), d)
    ex_by_site = Counter()
    for e in extracts:
        sid = p_by_id.get(e["posting_id"], {}).get("site_id")
        if sid:
            ex_by_site[(e["account_id"], sid)] += 1
    units_by_site = Counter((u["account_id"], u["site_id"]) for u in units.values() if u["site_id"])

    have_corpus = {r["account_id"] for r in postings}
    cov = []
    for s in sites:
        aid, sid = s["account_id"], s["site_id"]
        if a.account and aid not in set(a.account):
            continue
        k = (aid, sid)
        n_idx = idx_by_site[k]
        if aid not in have_corpus:
            state = "never_scanned"
        elif n_idx == 0:
            state = "scanned_none_found"
        elif ex_by_site[k] == 0:
            state = "indexed_not_extracted"
        else:
            state = "covered"
        cov.append({"account_id": aid, "site_id": sid, "site_name": s.get("site", ""),
                    "city": s.get("city", ""), "icp_fit": s.get("icp_fit", ""),
                    "n_indexed": str(n_idx), "n_admitted": str(adm_by_site[k]),
                    "n_bodies": str(body_by_site[k]), "n_extracts": str(ex_by_site[k]),
                    "n_units": str(units_by_site[k]),
                    "first_seen": first_by.get(k, ""), "last_seen": last_by.get(k, ""),
                    "coverage_state": state, "scanned_at": aiq.today() if aid in have_corpus else ""})

    aiq.write("org_units.csv", sorted(units.values(),
                                     key=lambda u: (u["account_id"], -int(u["n_postings"]),
                                                    u["unit_slug"])), UNIT_FIELDS)
    aiq.write("org_edges.csv", sorted(edges.values(),
                                      key=lambda e: (e["account_id"], e["edge_type"],
                                                     e["src_unit_id"])), EDGE_FIELDS)
    aiq.write("org_site_coverage.csv", cov, COV_FIELDS)

    obs = [u for u in units.values() if u["unit_kind"] == "observed"]
    print("=== build_org ===")
    print("org units    : %d  (%d observed, %d named-but-unmapped ghosts)"
          % (len(units), len(obs), len(units) - len(obs)))
    print("org edges    : %d " % len(edges), dict(Counter(e["edge_type"] for e in edges.values())))
    print("confidence   :", dict(Counter(e["confidence"] for e in edges.values())))
    print("site coverage:", dict(Counter(c["coverage_state"] for c in cov)))
    gaps = Counter()
    for u in units.values():
        for g in u["gap_flags"].split("|"):
            if g:
                gaps[g] += 1
    print("unit gaps    :", dict(gaps))
    if a.report:
        for u in sorted(obs, key=lambda x: -int(x["n_postings"]))[:14]:
            print("  %-46s %-9s n=%s site=%-22s parent=%s"
                  % (u["unit_name_display"][:46] + (" (%s)" % u["acronym"] if u["acronym"] else ""),
                     u["confidence"], u["n_postings"], u["site_id"] or "-",
                     u["parent_label"] or "(none observed)"))


if __name__ == "__main__":
    main()

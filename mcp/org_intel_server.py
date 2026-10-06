#!/usr/bin/env python3
"""
Org-intelligence MCP server. STRUCTURED RECORDS ONLY.

This exists under a specific constraint. The rep team rejected the interaction pattern of "ask a
question, get a paragraph, paste it into an email" -- in their words, the worry was that an MCP
surface "ends up being tell me what I need to know and write copy". So this server is built to be
structurally incapable of that:

  * No tool accepts a natural-language question. Every input is a typed filter.
  * No tool returns a summary, an answer, an analysis or a recommendation field. There is nowhere
    in any response schema for a paragraph to live.
  * Every returned claim carries its provenance ids, so the caller can always get to the posting,
    the date and the verbatim line.
  * Nothing quarantined by the verbatim gate is ever served.

If the caller wants a narrative, it has to write one itself and it will have the citations to
support it. That is the intended division of labour: the tool supplies context, the human supplies
judgement.

Register with:
  claude mcp add org-intel -- python3 "<abs path>/mcp/org_intel_server.py"
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import aiq  # noqa: E402

PROTO = "2024-11-05"
NAME = "org-intel"
VERSION = "1.0.0"

_cache = {}


def T(name):
    if name not in _cache:
        _cache[name] = aiq.load(name + ".csv")
    return _cache[name]


def _pick(row, keys):
    return {k: row.get(k, "") for k in keys}


# ---------------------------------------------------------------- tool implementations
UNIT_KEYS = ["unit_id", "account_id", "unit_name_display", "acronym", "unit_kind", "site_id",
             "parent_unit_id", "parent_label", "n_postings", "tech_stack", "job_families",
             "first_evidence_date", "last_evidence_date", "confidence", "confidence_basis",
             "gap_flags", "human_state", "evidence_quote_ids"]
EDGE_KEYS = ["edge_id", "account_id", "edge_type", "src_unit_id", "dst_unit_id", "dst_site_id",
             "src_label", "dst_label", "n_postings", "n_independent_postings",
             "first_evidence_date", "last_evidence_date", "confidence", "confidence_basis",
             "status", "human_state", "evidence_quote_ids"]
POST_KEYS = ["posting_id", "account_id", "site_id", "site_resolution", "city_raw", "role_title",
             "job_family", "seniority_rank", "url", "first_seen", "last_seen", "posted_date",
             "posted_date_source", "extraction_tier", "tier2_admit", "tier2_score"]


def list_org_units(account_id=None, site_id=None, min_confidence=None, include_unmapped=True,
                   limit=200):
    rank = {"low": 1, "medium": 2, "high": 3, "human": 4}
    out = []
    for u in T("org_units"):
        if u.get("status") == "rejected":
            continue
        if account_id and u["account_id"] != account_id:
            continue
        if site_id and u.get("site_id") != site_id:
            continue
        if not include_unmapped and u.get("unit_kind") == "named_not_mapped":
            continue
        if min_confidence and rank.get(u.get("confidence"), 0) < rank.get(min_confidence, 0):
            continue
        out.append(_pick(u, UNIT_KEYS))
    return {"records": out[:limit], "count": len(out),
            "note_fields": {"unit_kind": "named_not_mapped means a posting named this org but no "
                                         "unit of its own was reconstructed",
                            "confidence": "derived from evidence volume, never asserted"}}


def get_org_unit(unit_id):
    for u in T("org_units"):
        if u["unit_id"] == unit_id:
            rec = _pick(u, UNIT_KEYS)
            rec["edges"] = [_pick(e, EDGE_KEYS) for e in T("org_edges")
                            if e["src_unit_id"] == unit_id or e["dst_unit_id"] == unit_id]
            rec["provenance"] = get_provenance(unit_id=unit_id)["records"]
            return {"records": [rec], "count": 1}
    return {"records": [], "count": 0, "error": "no unit with that unit_id"}


def list_org_edges(account_id=None, edge_type=None, min_confidence=None, limit=400):
    rank = {"low": 1, "medium": 2, "high": 3, "human": 4}
    out = []
    for e in T("org_edges"):
        if e.get("status") == "rejected":
            continue
        if account_id and e["account_id"] != account_id:
            continue
        if edge_type and e["edge_type"] != edge_type:
            continue
        if min_confidence and rank.get(e.get("confidence"), 0) < rank.get(min_confidence, 0):
            continue
        out.append(_pick(e, EDGE_KEYS))
    return {"records": out[:limit], "count": len(out)}


def get_provenance(quote_ids=None, unit_id=None, edge_id=None, limit=60):
    """The citation endpoint. Only quotes that passed the verbatim gate are ever returned."""
    want = set(quote_ids or [])
    if unit_id:
        for u in T("org_units"):
            if u["unit_id"] == unit_id:
                want |= {q for q in (u.get("evidence_quote_ids") or "").split("|") if q}
    if edge_id:
        for e in T("org_edges"):
            if e["edge_id"] == edge_id:
                want |= {q for q in (e.get("evidence_quote_ids") or "").split("|") if q}
    p_by = {p["posting_id"]: p for p in T("postings")}
    out = []
    for q in T("posting_quotes"):
        if q.get("verbatim_verified") != "true":
            continue                      # quarantined claims are never served
        if want and q["quote_id"] not in want:
            continue
        p = p_by.get(q["posting_id"], {})
        out.append({"quote_id": q["quote_id"], "posting_id": q["posting_id"],
                    "claim_type": q["claim_type"], "subject": q["subject"],
                    "quote_text": q["quote_text"], "match_method": q.get("match_method", ""),
                    "display_date": q.get("display_date", ""), "date_kind": q.get("date_kind", ""),
                    "role_title": p.get("role_title", ""), "url": p.get("url", ""),
                    "site_id": p.get("site_id", "")})
    return {"records": out[:limit], "count": len(out),
            "note_fields": {"date_kind": "cdx_capture means a Wayback first-capture date, NOT the "
                                         "date the job was posted"}}


def query_postings(account_id=None, site_id=None, job_family=None, seen_since=None,
                   names_tool=None, has_org_structure=None, min_seniority=None, limit=200):
    ex = {e["posting_id"]: e for e in T("posting_extracts")}
    out = []
    for p in T("postings"):
        if account_id and p["account_id"] != account_id and p.get("mirror_account_id") != account_id:
            continue
        if site_id and p.get("site_id") != site_id:
            continue
        if job_family and p.get("job_family") != job_family:
            continue
        if seen_since and (p.get("last_seen") or "") < seen_since:
            continue
        if min_seniority and int(p.get("seniority_rank") or 1) < int(min_seniority):
            continue
        e = ex.get(p["posting_id"])
        if has_org_structure is True and not e:
            continue
        if has_org_structure is False and e:
            continue
        if names_tool:
            tech = (e or {}).get("tech_stack", "")
            if names_tool.lower() not in tech.lower():
                continue
        rec = _pick(p, POST_KEYS)
        if e:
            rec["team_name"] = e.get("team_name", "")
            rec["parent_org"] = e.get("parent_org", "")
            rec["tech_stack"] = e.get("tech_stack", "")
        out.append(rec)
    out.sort(key=lambda r: -int(r.get("tier2_score") or 0))
    return {"records": out[:limit], "count": len(out)}


def get_hiring_mix(account_id, scope="account_period_family"):
    out = [_pick(r, ["scope", "account_id", "site_id", "period", "dim_kind", "dim_key", "n",
                     "denom", "share", "self_index", "peer_median_share", "peer_n_accounts",
                     "peer_index", "bias_flag", "bias_note"])
           for r in T("posting_rollup")
           if r["account_id"] == account_id and r["scope"] == scope]
    return {"records": out, "count": len(out),
            "note_fields": {"n": "raw capture count -- NOT comparable across periods, because the "
                                 "denominator tracks Wayback crawl density",
                            "share": "use this, not n",
                            "peer_index": "share divided by the median share across accounts in the "
                                          "same quarter; the crawl regime divides out",
                            "bias_note": "printed caveat for this cell when bias_flag is set"}}


def list_gaps(account_id=None, limit=400):
    units, cov = [], []
    for u in T("org_units"):
        if account_id and u["account_id"] != account_id:
            continue
        if u.get("gap_flags"):
            units.append({"unit_id": u["unit_id"], "unit_name_display": u["unit_name_display"],
                          "site_id": u.get("site_id", ""), "gap_flags": u["gap_flags"],
                          "n_postings": u.get("n_postings", "")})
    for c in T("org_site_coverage"):
        if account_id and c["account_id"] != account_id:
            continue
        if c["coverage_state"] != "covered":
            cov.append(_pick(c, ["account_id", "site_id", "site_name", "city", "icp_fit",
                                 "n_indexed", "n_admitted", "n_bodies", "coverage_state"]))
    return {"records": {"units_with_gaps": units[:limit], "sites_not_covered": cov[:limit]},
            "count": len(units) + len(cov),
            "note_fields": {"coverage_state": "never_scanned means nobody looked; "
                                              "scanned_none_found means we looked and found nothing"}}


def get_vocabulary(account_id, unit_id=None, limit=120):
    """Verbatim phrases in which a team describes its own work. Messaging input, quoted not summarised."""
    out = []
    units = {u["unit_id"]: u for u in T("org_units")}
    for q in T("posting_quotes"):
        if q.get("verbatim_verified") != "true" or q["claim_type"] != "vocabulary":
            continue
        if account_id and q["account_id"] != account_id:
            continue
        out.append({"quote_id": q["quote_id"], "posting_id": q["posting_id"],
                    "quote_text": q["quote_text"], "display_date": q.get("display_date", "")})
    if unit_id:
        u = units.get(unit_id) or {}
        keep = {x for x in (u.get("evidence_posting_ids") or "").split("|") if x}
        out = [r for r in out if r["posting_id"] in keep]
    for u in units.values():
        if account_id and u["account_id"] != account_id:
            continue
        if unit_id and u["unit_id"] != unit_id:
            continue
        for v in (u.get("vocabulary") or "").split("|"):
            if v:
                out.append({"quote_id": "", "posting_id": "", "quote_text": v,
                            "display_date": u.get("last_evidence_date", "")})
    return {"records": out[:limit], "count": len(out)}


def list_accounts():
    cov = {}
    for c in T("org_site_coverage"):
        a = cov.setdefault(c["account_id"], {"sites": 0, "covered": 0})
        a["sites"] += 1
        if c["coverage_state"] == "covered":
            a["covered"] += 1
    units = {}
    for u in T("org_units"):
        if u.get("unit_kind") == "observed":
            units[u["account_id"]] = units.get(u["account_id"], 0) + 1
    posts = {}
    for p in T("postings"):
        posts[p["account_id"]] = posts.get(p["account_id"], 0) + 1
    out = []
    for a in T("accounts"):
        if a.get("active") != "true" or a.get("in_universe") != "true":
            continue
        c = cov.get(a["account_id"], {"sites": 0, "covered": 0})
        out.append({"account_id": a["account_id"], "company": a["company"], "hq": a.get("hq", ""),
                    "sites": c["sites"], "sites_covered": c["covered"],
                    "org_units": units.get(a["account_id"], 0),
                    "postings_indexed": posts.get(a["account_id"], 0)})
    out.sort(key=lambda r: -r["org_units"])
    return {"records": out, "count": len(out)}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}

TOOLS = [
    ("list_accounts", "List target accounts with org-reconstruction coverage counts.",
     {"type": "object", "properties": {}, "additionalProperties": False}, list_accounts),
    ("list_org_units",
     "List reconstructed org units (teams) with their parent, site, stack and confidence. Returns "
     "records only.",
     {"type": "object", "properties": {
         "account_id": S, "site_id": S,
         "min_confidence": {"type": "string", "enum": ["low", "medium", "high", "human"]},
         "include_unmapped": B, "limit": I}, "additionalProperties": False}, list_org_units),
    ("get_org_unit", "One org unit with its edges and full provenance records.",
     {"type": "object", "properties": {"unit_id": S}, "required": ["unit_id"],
      "additionalProperties": False}, get_org_unit),
    ("list_org_edges",
     "List org edges (reports_to / supports / located_at) with evidence counts and confidence.",
     {"type": "object", "properties": {
         "account_id": S, "edge_type": {"type": "string",
                                        "enum": ["reports_to", "supports", "located_at"]},
         "min_confidence": {"type": "string", "enum": ["low", "medium", "high", "human"]},
         "limit": I}, "additionalProperties": False}, list_org_edges),
    ("get_provenance",
     "The citation endpoint: verbatim quote, claim type, posting URL and date behind any unit or "
     "edge. Only quotes that passed the verbatim gate are returned.",
     {"type": "object", "properties": {
         "quote_ids": {"type": "array", "items": S}, "unit_id": S, "edge_id": S, "limit": I},
      "additionalProperties": False}, get_provenance),
    ("query_postings",
     "Filter the indexed job-requisition corpus by site, family, seniority, time window, named "
     "tool, or whether org structure was extracted.",
     {"type": "object", "properties": {
         "account_id": S, "site_id": S, "job_family": S, "seen_since": S, "names_tool": S,
         "has_org_structure": B, "min_seniority": I, "limit": I},
      "additionalProperties": False}, query_postings),
    ("get_hiring_mix",
     "Job-family share of requisitions by quarter, with peer-baseline index and crawl-density bias "
     "flags. Use share and peer_index, not raw n.",
     {"type": "object", "properties": {"account_id": S, "scope": S}, "required": ["account_id"],
      "additionalProperties": False}, get_hiring_mix),
    ("list_gaps",
     "What is missing: units with no observed parent or no known stack, and sites never scanned.",
     {"type": "object", "properties": {"account_id": S, "limit": I},
      "additionalProperties": False}, list_gaps),
    ("get_vocabulary",
     "Verbatim phrases in which a team describes its own work, for messaging. Quotes, not summaries.",
     {"type": "object", "properties": {"account_id": S, "unit_id": S, "limit": I},
      "required": ["account_id"], "additionalProperties": False}, get_vocabulary),
]
IMPL = {t[0]: t[3] for t in TOOLS}


def handle(req):
    m = req.get("method")
    rid = req.get("id")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTO,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": NAME, "version": VERSION},
            "instructions": ("Structured records only. No tool here returns a summary, an answer or "
                             "a recommendation, and none accepts a natural-language question -- by "
                             "design. Build conclusions yourself and cite them with "
                             "get_provenance, which returns the verbatim line, the posting URL and "
                             "the date behind every claim. Note that date_kind=cdx_capture is a "
                             "Wayback first-capture date, not a posted date, and that raw "
                             "requisition counts are not comparable across quarters -- use share "
                             "and peer_index from get_hiring_mix.")}}
    if m in ("notifications/initialized", "initialized"):
        return None
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOLS]}}
    if m == "tools/call":
        p = req.get("params") or {}
        name = p.get("name")
        args = p.get("arguments") or {}
        fn = IMPL.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": "unknown tool %r" % name}}
        try:
            out = fn(**args)
        except TypeError as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": "bad arguments: %s" % e}}
        except Exception as e:  # noqa: BLE001 - surface the failure as data, never crash the server
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": "%s: %s" % (type(e).__name__, e)}}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(out, indent=1)}],
            "structuredContent": out, "isError": False}}
    if m == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "unknown method %r" % m}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

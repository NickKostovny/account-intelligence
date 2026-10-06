#!/usr/bin/env python3
"""
Render one per-account brief from the SSOT, with the ORG MAP as the primary view.

Rewritten from the prose version. The rule that drives every choice here: no paragraphs. The reps
this is for asked for "any of these big paragraphs into bullets... that way you can scan it, you
understand what's there", and they explicitly rejected AI-written conclusions. So:

  * Every former prose block becomes bullets, chips, a ledger, a timeline or a meter.
  * The only element allowed to contain a sentence is a <blockquote> inside .ev-src -- somebody
    else's words, quoted, dated and linked. Prose is evidence, never conclusion.
  * guard() raises on any over-length string, and the build ASSERTS the page contains no <p> tag
    before writing. A regression fails the build instead of shipping.
  * Absence is rendered, not omitted: a never-swept site gets a hatched bar, a team with no known
    stack gets a dashed "stack ?" chip, an unparented unit gets a dead stub into empty space.

  python3 build-briefs.py
  python3 build-briefs.py --account astrazeneca
"""
import argparse, html, json, os, re, sys, time
from collections import defaultdict, Counter

import aiq
import orgmap
from orgmap import CSS, esc, render_tree

# Sales-facing layout additions (2026-09-10): site verdict badges, Clay-verified head links, index groups.
CSS = CSS + r"""
.cf-keep{background:var(--ok)}.cf-flag{background:var(--sched)}
.cf-gap{background:var(--pending);color:var(--ink)}.cf-drop{background:var(--ghost);color:var(--ink)}
.head{font-size:12.5px;color:var(--body);line-height:1.4;max-width:46ch}
.head a{color:var(--link);font-weight:600;text-decoration:none}.head a:hover{text-decoration:underline}
.head .ht{color:var(--muted)}.head .hm{display:block;color:var(--label);font-size:11px;margin-top:3px}
.head .hx{color:var(--label);font-size:11px}
.vleg{font-size:12px;color:var(--muted);margin:8px 0 0;display:flex;gap:16px;flex-wrap:wrap;align-items:center}
.vleg>span{display:inline-flex;gap:6px;align-items:center}
.grp{margin-top:28px}.grp .eyebrow{margin-bottom:6px}
td.headc{min-width:240px;width:26%}td.sitec{min-width:170px}
.cf-closed{background:var(--ghost);color:var(--ink)}
.cf-review{background:transparent;color:var(--muted);border:1px dashed var(--ghost)}
.jc{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
a.chip{text-decoration:none;color:inherit} a.chip:hover{border-color:var(--ink,#1c363c)} .chip.jt{font-size:11px;padding:1px 7px}
td.fitc{min-width:150px}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
"""

OUT = os.path.join(aiq.HERE, "briefs")
E = esc


# ---------------------------------------------------------------- prose control
class ProseError(SystemExit):
    pass


def guard(s, maxwords, where):
    n = len((s or "").split())
    if n > maxwords:
        raise ProseError("PROSE GUARD %s: cap %dw, got %dw :: %s…" % (where, maxwords, n, (s or "")[:90]))
    return s


def shorten(s, maxwords):
    w = (s or "").split()
    return " ".join(w[:maxwords]) + ("…" if len(w) > maxwords else "")


def bullets(s, maxwords=13, cap=5):
    """Prose -> scannable fragments. Splits on semicolons, bullets, and sentence boundaries."""
    if not s:
        return []
    parts = re.split(r"[;•]|(?<=[a-z0-9\)])\.\s+(?=[A-Z])", s)
    out = []
    for p in parts:
        p = p.strip(" ·—-.\t")
        if len(p.split()) < 3:
            continue
        out.append(shorten(p, maxwords))
        if len(out) == cap:
            break
    return out


def chips(s, sep=r"[,;/]", cap=10, maxwords=6):
    if not s:
        return []
    out = []
    for p in re.split(sep, s):
        p = p.strip()
        if not p or len(p.split()) > maxwords:
            continue
        if p not in out:
            out.append(p)
        if len(out) == cap:
            break
    return out


def chip_row(items, label=""):
    if not items:
        return '<div class="empty">not observed</div>'
    lb = '<b>%s</b>' % E(label) if label else ""
    return '<div class="chips">%s</div>' % "".join(
        '<span class="chip">%s%s</span>' % (lb if i == 0 else "", E(x)) for i, x in enumerate(items))


def cf(conf):
    c = (conf or "").lower() or "low"
    return '<span class="cf cf-%s">%s</span>' % (E(c), E(c))


# "medium" on its own told the reader nothing. Confidence is derived from evidence volume, so say so
# in one line and drop the separate basis chip that used to carry the sentence.
def conf_line(conf, basis):
    c = (conf or "").lower() or "low"
    word = {"human": "CONFIRMED BY A PERSON"}.get(c, "%s CONFIDENCE" % c.upper())
    b = (" · " + E(basis)) if basis else ""
    return '<span class="cf cf-%s">%s</span><span class="conf-b">%s</span>' % (E(c), E(word), b)


EDGE_LABEL = {"reports_to": "REPORTING LINE", "supports": "SUPPORTS (LATERAL)",
              "located_at": "LOCATED AT", "staffed_by": "STAFFED BY", "same_as": "SAME TEAM AS"}

# The raw gap tokens are internal names. Say what each one means to a reader.
GAP_LABEL = {
    "no_parent": "no reporting line found",
    "no_site": "no site resolved",
    "site_split": "postings span more than one site",
    "site_ambiguous": "site ambiguous",
    "single_posting": "seen in one posting only",
    "no_tech": "no systems named",
    "no_named_people": "no person named",
    "acronym_unexpanded": "acronym never spelled out",
    "archive_only_evidence": "from archived postings only",
    "mention_only": "inferred — named in a posting only",
    "city_not_in_sites": "location not in our site map",
}


def src_block(src):
    """
    First source expanded, the rest folded away. A panel with eight quote blocks is mostly scrolling.

    The <details> lives INSIDE the <ol class="ev-src"> on purpose: the prose guard locates the span
    between that literal opening tag and the next </ol> and requires every <blockquote> to fall
    inside it. Wrapping the <ol> in a <details> instead would put the folded quotes outside the span
    and fail the build.
    """
    if not src:
        return '<div class="empty">No verified quote on file for this team.</div>'
    if len(src) == 1:
        return '<ol class="ev-src">%s</ol>' % src[0]
    return ('<ol class="ev-src">%s<details class="more"><summary>%d more source%s</summary>%s'
            '</details></ol>' % (src[0], len(src) - 1, "" if len(src) == 2 else "s",
                                 "".join(src[1:])))


# ---------------------------------------------------------------- data
def load_all():
    d = {}
    for n in ["accounts", "sites", "people", "signals", "postings", "posting_extracts",
              "posting_quotes", "org_units", "org_edges", "org_site_coverage", "posting_rollup",
              "unmatched_cities", "careers"]:
        d[n] = aiq.load(n + ".csv")
    # Clay-verified site heads (merge_clay.py). Optional: the brief renders without the file.
    sh = os.path.join(aiq.DATA, "site_heads.csv")
    d["site_heads"] = aiq.load("site_heads.csv") if os.path.exists(sh) else []
    d["overrides"] = aiq.load_overrides()
    d["jev"] = load_jev(d["sites"])
    return d


# ---------------------------------------------------------------- Jev layers (data/jev/, 2026-09-28)
# Every layer is optional: a missing or unreadable file renders nothing. Keyed lookups only; the
# page never shows the model name -- provenance rides in title attributes.
JEV_DIR = os.path.join(aiq.DATA, "jev")
JEV_LAYERS = [("site_fit", "site_id"), ("site_profile", "site_id"), ("site_hiring", "site_id"),
              ("account_profile", "account_id"), ("account_hiring", "account_id"),
              ("digital_it_location", "account_id"), ("publication_relevance", "signal_id"),
              ("site_verdict_final", "site_id")]
# news (data/news/, 2026-09-30): collected by news_collect.py, labelled by jev_news.py
NEWS_DIR = os.path.join(aiq.DATA, "news")
NEWS_LAYERS = [("site_news", "site_id"), ("account_news", "account_id")]


def load_jev(sites):
    site_by_id = {s["site_id"]: s for s in sites}
    j = {}
    for name, key in JEV_LAYERS:
        try:
            rows = aiq.load(name + ".csv", JEV_DIR)
        except Exception as e:  # a malformed layer must not take the briefs down
            print("jev layer %s skipped: %s" % (name, e), file=sys.stderr)
            rows = []
        j[name] = {r[key]: r for r in rows if r.get(key)}
    for name, key in NEWS_LAYERS:
        try:
            rows = aiq.load(name + ".csv", NEWS_DIR)
        except Exception as e:
            print("news layer %s skipped: %s" % (name, e), file=sys.stderr)
            rows = []
        j[name] = {r[key]: r for r in rows if r.get(key)}
    # site_id is not durable across normalize.py runs. The site-level layers carry the site text or
    # the account; a row that no longer matches sites.csv is stale and is dropped, not mis-joined.
    stale = set()
    for name in ("site_profile", "site_hiring"):
        for sid, r in j[name].items():
            s = site_by_id.get(sid)
            if not s or s.get("site") != r.get("site") or s.get("account_id") != r.get("account_id"):
                stale.add(sid)
    for name in ("site_fit", "site_verdict_final", "site_news"):
        for sid, r in j[name].items():
            s = site_by_id.get(sid)
            if not s or s.get("account_id") != r.get("account_id"):
                stale.add(sid)
    for name in ("site_fit", "site_profile", "site_hiring", "site_verdict_final", "site_news"):
        for sid in stale & set(j[name]):
            del j[name][sid]
    j["stale_sites"] = len(stale)
    return j


def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def inum(x):
    return int(fnum(x))


def model_of(r):
    return (r or {}).get("model") or "jev"


def jchip(text, title="", label="", cls="chip jt"):
    return '<span class="%s"%s>%s%s</span>' % (
        cls, (' title="%s"' % E(title)) if title else "", ('<b>%s</b>' % E(label)) if label else "",
        E(text))


def fit_cell(f, fin=None):
    """Site verdict + reason tag. The final verdict (checked against a source, or two readings
    agree; data/jev/site_verdict_final.csv) wins over the raw Jev reading. REVIEW renders as
    'review', never a tag."""
    if fin:
        v = (fin.get("verdict") or "").upper()
        parts = ['<span class="cf cf-%s">%s</span>' % (E(v.lower()), E("review" if v == "REVIEW" else v))]
        tag = fin.get("reason") or ""
        if tag and v != "REVIEW" and not tag.endswith("?") and tag.lower() != v.lower():
            parts.append(jchip(tag))
        if fin.get("source_url"):
            parts.append('<a class="chip jt" href="%s" target="_blank" rel="noopener">source</a>' % E(fin["source_url"]))
        title = " · ".join(x for x in [fin.get("basis", ""), fin.get("source_date", ""),
                                        "jev %s / map %s" % (fin.get("jev_verdict", ""), fin.get("policy_verdict", ""))] if x)
        return '<td class="fitc" title="%s"><div class="jc">%s</div></td>' % (E(title), "".join(parts))
    v = ((f or {}).get("jev_verdict") or "").upper()
    if not v:
        return '<td class="fitc"><span class="empty">—</span></td>'
    tag = f.get("reason_tag") or ""
    parts = ['<span class="cf cf-%s">%s</span>' % (E(v.lower()), E("review" if v == "REVIEW" else v))]
    if tag and v != "REVIEW" and not tag.endswith("?") and tag.lower() != v.lower():
        parts.append(jchip(tag))
    if f.get("info_tag") == "CDMO" and "CDMO" not in tag:
        parts.append(jchip("CDMO"))
    ps = " ".join("%s p=%s" % (k, f[k + "_p"]) for k in ("pd_msat", "drug_substance", "drug_product",
                                                      "manufacturing") if f.get(k + "_p"))
    title = " · ".join(x for x in [f.get("explanation", ""), model_of(f), ps] if x)
    return '<td class="fitc" title="%s"><div class="jc">%s</div></td>' % (E(title), "".join(parts))


def profile_chips_cell(prof, kind, fallback=None):
    """Decisive-yes chips from site_profile ('fn' or 'mod'); unsure answers stay hidden."""
    col = "function_chips" if kind == "fn" else "modality_chips"
    items = [x for x in ((prof or {}).get(col) or "").split("|") if x]
    if not items:
        if fallback:
            return '<td>%s</td>' % "".join('<span class="chip">%s</span>' % E(x) for x in fallback)
        return '<td><span class="empty">—</span></td>'
    ps = []
    for k in sorted(prof):
        m = re.match(r"^%s_(.+)_p$" % kind, k)
        if m and prof.get(k[:-2]) == "yes":
            ps.append("%s p=%s" % (m.group(1).replace("_", " "), prof[k]))
    return '<td title="%s"><div class="jc">%s</div></td>' % (
        E(" · ".join([model_of(prof)] + ps)), "".join(jchip(x) for x in items))


HIRE_FAM = [("n_process_dev", "PD"), ("n_msat_mfg_sci", "MSAT"), ("n_cell_gene", "cell/gene"),
            ("n_analytical", "analytical"), ("n_automation_mes", "automation/MES"),
            ("n_lab_systems", "lab systems"), ("n_data_digital", "data")]


def hiring_text(h):
    """Named-family counts only; the other/unsure buckets are never shown as a function."""
    return " · ".join("%s %d" % (lab, inum(h.get(k))) for k, lab in HIRE_FAM if inum((h or {}).get(k)))


def hiring_cell(h):
    """One small chip per named family so the cell wraps instead of widening the table."""
    parts = ["%s %d" % (lab, inum(h.get(k))) for k, lab in HIRE_FAM if inum((h or {}).get(k))]
    if not parts:
        return "<td></td>"
    return '<td title="%s"><div class="jc">%s</div></td>' % (
        E("%s · %s postings · newest %s" % (model_of(h), h.get("postings", ""), h.get("newest_last_seen", ""))),
        "".join(jchip(x) for x in parts))


def news_chip(item):
    """'capex · 2026-08' linking to the article; the headline and source ride in the title."""
    d = (item.get("published") or item.get("latest_date") or "")[:7]
    trig = item.get("trigger") or item.get("latest_trigger") or ""
    url = item.get("url") or item.get("latest_url") or ""
    title = " · ".join(x for x in [item.get("title") or item.get("latest_title") or "",
                                   item.get("source") or item.get("latest_source") or ""] if x)
    return '<a class="chip jt" href="%s" target="_blank" rel="noopener" title="%s">%s</a>' % (
        E(url), E(title), E(" · ".join(x for x in (trig, d) if x)))


def news_cell(r):
    if not r:
        return "<td></td>"
    return '<td><div class="jc">%s</div></td>' % news_chip(r)


def modality_chips(ap):
    """Account footprint: sites with a decisive yes per modality, e.g. 'mAbs 4'."""
    out = []
    for part in ((ap or {}).get("modality_footprint_jev") or "").split("|"):
        name, _, n = part.rpartition(":")
        if name and inum(n):
            out.append("%s %d" % (name, inum(n)))
    return out


def digital_chip(r, site_by_id):
    """Where digital/data/IT reqs sit, as the code label. Thin evidence is not a finding: no chip."""
    lab = (r or {}).get("location_label") or ""
    if not lab or lab.startswith(("no postings", "no digital", "thin")):
        return ""
    n = inum(r.get("n_digital"))
    top = r.get("top_location") or ""
    if r.get("top_kind") == "site" and r.get("top_id") in site_by_id:
        top = site_by_id[r["top_id"]].get("city") or top
    top = re.sub(r"\s*\((city|site)\)\s*$", "", top).strip()
    share = int(round(100 * fnum(r.get("top_share"))))
    loc = ""
    if lab.startswith("concentrated") and top:
        loc = "concentrated · %s %d%%" % (top, share)
    elif lab.startswith("spread"):
        loc = "spread · %d locations" % inum(r.get("n_locations"))
        if top:
            loc += " · largest %s %d%%" % (top, share)
    text = "{:,} reqs".format(n) + ((" · " + loc) if loc else "")
    title = " · ".join(x for x in [model_of(r), r.get("caveat", ""),
                                   ("global-titled %s" % r["global_titled_display"])
                                   if r.get("global_titled_display") else ""] if x)
    return jchip(text, title, "Digital/IT", cls="chip")


def spark(quarters, counts, w=46, h=13):
    if not counts:
        return '<svg class="spark" viewBox="0 0 %d %d" aria-hidden="true"></svg>' % (w, h)
    mx = max(counts) or 1
    med = sorted(counts)[len(counts) // 2]
    bw = max(2.0, (w - 2) / float(len(counts)) - 1)
    bars = []
    for i, c in enumerate(counts):
        bh = max(1.0, h * (c / float(mx)))
        cls = ' class="hot"' if c > med and c == mx else ""
        bars.append('<rect%s x="%.1f" y="%.1f" width="%.1f" height="%.1f"/>'
                    % (cls, 1 + i * (bw + 1), h - bh, bw, bh))
    return '<svg class="spark" viewBox="0 0 %d %d" aria-hidden="true">%s</svg>' % (w, h, "".join(bars))


def dock_unit(u, quotes_by_unit, edges_by_src, site_by_id, postings_by_id):
    """The provenance panel. :target driven, so it is zero-JS and deep-linkable."""
    qs = quotes_by_unit.get(u["unit_id"], [])
    facts = []

    def fact(k, v, missing_hint=""):
        if v:
            facts.append('<li><span class="k">%s</span><span class="v">%s</span></li>' % (E(k), v))
        else:
            facts.append('<li class="miss"><span class="k">%s</span><span class="v">%s</span></li>'
                         % (E(k), E(missing_hint or "not observed")))

    site = site_by_id.get(u["site_id"], {})
    fact("Site", E(site.get("site", "")) + (' <span class="cf cf-low">%s</span>' % E(u["site_resolution"])
                                            if u["site_resolution"] == "modal_of_postings" else ""),
         "no site resolved from the postings")
    fact("Parent", (('%s <a href="#ev-%s">↗</a>' % (E(u["parent_label"]), E(u["parent_unit_id"])))
                    if u["parent_unit_id"] else ""), "no reporting line observed")
    sup = [e for e in edges_by_src.get(u["unit_id"], []) if e["edge_type"] == "supports"]
    fact("Supports", " · ".join(E(e["dst_label"]) for e in sup[:6]), "no lateral teams named")
    fact("Stack", " · ".join(E(t) for t in (u["tech_stack"] or "").split("|") if t),
         "no systems named in any posting")
    fact("Hiring mgr", " · ".join(E(h) for h in (u["hiring_managers"] or "").split("|") if h),
         "no posting named one")
    fact("Reqs observed", '<span class="num">%s</span>' % E(u["n_postings"]))
    fact("Evidence window", "%s → %s" % (E(u["first_evidence_date"]), E(u["last_evidence_date"]))
         if u["first_evidence_date"] else "")

    src = []
    for q in qs[:8]:
        p = postings_by_id.get(q["posting_id"], {})
        unver = "" if q.get("verbatim_verified") == "true" else " unver"
        note = ("" if q.get("verbatim_verified") == "true"
                else '<div class="vb-l">UNVERIFIED — could not be matched in the fetched body</div>')
        dk = q.get("date_kind") or ""
        dlab = E(q.get("display_date") or "") + (" (crawl date, not posted date)"
                                                 if dk == "cdx_capture" else "")
        src.append('<li><div class="src-h"><span class="d num">%s</span>%s'
                   '<a href="%s" target="_blank" rel="noopener">source ↗</a></div>'
                   '<div class="src-t">%s</div>%s<div class="vb-l">VERBATIM · %s</div>'
                   '<blockquote class="vb%s">%s</blockquote></li>'
                   % (dlab, cf("high" if q.get("verbatim_verified") == "true" else "low"),
                      E(p.get("url", "")), E(p.get("role_title", "")), note,
                      E(q.get("claim_type", "")), unver, E(q.get("quote_text", ""))))

    gaps = [g for g in (u["gap_flags"] or "").split("|") if g]
    inf = ""
    if u["parent_unit_id"] and qs:
        inf = ('<div class="ev-inf"><div class="inf-l">INFERRED — NOT STATED IN SOURCE</div>'
               '<ul><li>parent = %s, read from the quoted line(s) above</li></ul></div>'
               % E(u["parent_label"]))

    return ('<aside class="ev" id="ev-%s"><div class="evbox">'
            '<div class="ev-h"><span class="ev-kind">TEAM</span>'
            '<a class="ev-x" href="#ev-none">✕</a></div>'
            '<div class="ev-t">%s%s</div>'
            '<div class="conf">%s</div>'
            '<div class="chips">%s</div>'
            '<ul class="ev-facts">%s</ul>'
            '%s%s'
            '<div class="ev-j" data-ref="%s"><button class="jb j-ok">Confirm</button>'
            '<button class="jb j-no">Reject</button><button class="jb j-pin">Pin</button>'
            '<input class="jn" placeholder="note…" maxlength="140"></div>'
            '</div></aside>'
            % (E(u["unit_id"]),
               E(u["unit_name_display"]),
               (' <span class="ac">%s</span>' % E(u["acronym"])) if u["acronym"] else "",
               conf_line(u["confidence"], u["confidence_basis"]),
               "".join('<span class="chip">%s</span>' % E(GAP_LABEL.get(g, g.replace("_", " ")))
                       for g in gaps),
               "".join(facts),
               src_block(src),
               inf, E(u["unit_id"])))


def dock_edge(e, quotes_by_id, postings_by_id):
    src = []
    for qid in [q for q in (e["evidence_quote_ids"] or "").split("|") if q][:6]:
        q = quotes_by_id.get(qid)
        if not q:
            continue
        p = postings_by_id.get(q["posting_id"], {})
        dk = q.get("date_kind") or ""
        src.append('<li><div class="src-h"><span class="d num">%s</span>%s'
                   '<a href="%s" target="_blank" rel="noopener">source ↗</a></div>'
                   '<div class="src-t">%s</div><div class="vb-l">VERBATIM</div>'
                   '<blockquote class="vb">%s</blockquote></li>'
                   % (E(q.get("display_date", "")) + (" (crawl date)" if dk == "cdx_capture" else ""),
                      cf("high"), E(p.get("url", "")), E(p.get("role_title", "")),
                      E(q.get("quote_text", ""))))
    conf = "human" if e.get("human_state") == "confirm" else e["confidence"]
    return ('<aside class="ev" id="ev-%s"><div class="evbox">'
            '<div class="ev-h"><span class="ev-kind">%s</span>'
            '<a class="ev-x" href="#ev-none">✕</a></div>'
            '<div class="ev-t">%s → %s</div>'
            '<div class="conf">%s</div>'
            '<ul class="ev-facts"><li><span class="k">Observations</span>'
            '<span class="v num">%s posting(s)</span></li>'
            '<li><span class="k">Window</span><span class="v">%s → %s</span></li></ul>'
            '%s'
            '<div class="ev-j" data-ref="%s"><button class="jb j-ok">Confirm</button>'
            '<button class="jb j-no">Reject</button>'
            '<input class="jn" placeholder="note…" maxlength="140"></div>'
            '</div></aside>'
            % (E(e["edge_id"]), E(EDGE_LABEL.get(e["edge_type"], e["edge_type"])),
               E(e["src_label"]), E(e["dst_label"]), conf_line(conf, e["confidence_basis"]),
               E(e["n_postings"]), E(e["first_evidence_date"]), E(e["last_evidence_date"]),
               src_block(src) if src else
               '<div class="empty">No verified quote — this line should not have rendered.</div>',
               E(e["edge_id"])))


def hiring_mix(aid, rollup):
    """
    Share-of-mix over time. Never counts: the CDX timestamp is a first-capture date, so raw
    per-quarter volume tracks how hard the archive crawled, not how hard the company hired. Rows
    carrying a bias flag print their precomputed caveat next to the number.
    """
    rows = [r for r in rollup if r["account_id"] == aid and r["scope"] == "account_period_family"]
    if not rows:
        return '<div class="empty">No indexed requisitions yet for this account.</div>'
    # 'unclassified' is every commercial, clinical and corporate req -- 79% of Biogen's and 83% of
    # AstraZeneca's. Ranked by share it took the top row and pushed the real families out of the
    # 6-row cap, which made the whole table read as noise. Excluded from the rows and reported as a
    # denominator instead, which is the honest form of the same fact.
    n_total = sum(int(r["n"] or 0) for r in rows)
    n_unc = sum(int(r["n"] or 0) for r in rows if r["dim_key"] == "unclassified")
    rows = [r for r in rows if r["dim_key"] != "unclassified"]
    if not rows:
        return ('<div class="empty">None of this account\'s %d captured requisitions fall into a '
                'bioprocess or data family yet.</div>' % n_total)
    periods = sorted({r["period"] for r in rows})[-8:]
    fam_tot = defaultdict(float)
    for r in rows:
        fam_tot[r["dim_key"]] += float(r["share"] or 0)
    fams = [f for f, _ in sorted(fam_tot.items(), key=lambda x: -x[1])][:6]
    by = {(r["dim_key"], r["period"]): r for r in rows}
    head = "".join("<th>%s</th>" % E(p) for p in periods)
    body = []
    for f in fams:
        cells = []
        for p in periods:
            r = by.get((f, p))
            if not r:
                cells.append('<td class="hatch-cell" title="no requisitions captured"></td>')
                continue
            sh = float(r["share"] or 0) * 100
            pi = float(r["peer_index"] or 0)
            flag = r["bias_flag"]
            title = r["bias_note"] or ("share %.1f%% · peer index %.2f (median of %s accounts)"
                                       % (sh, pi, r["peer_n_accounts"]))
            mark = " ⚑" if flag else ""
            cells.append('<td title="%s"><span class="num">%.0f%%</span>%s</td>'
                         % (E(title), sh, mark))
        body.append("<tr><td>%s</td>%s</tr>" % (E(f.replace("_", " ")), "".join(cells)))
    nb = sum(1 for r in rows if r["bias_flag"])
    note = ("" if not nb else
            '<div class="cov">⚑ %d quarter/family cell(s) carry a crawl-density caveat — hover the '
            'cell. Shares are comparable; raw counts are not.</div>' % nb)
    return ('<table><thead><tr><th>Job family</th>%s</tr></thead><tbody>%s</tbody></table>'
            '<div class="cov">Share of that account\'s requisitions captured in the quarter. '
            'Hatched = nothing captured.</div>%s' % (head, "".join(body), note))


def central_ledger(aid, postings, extracts, sites_by_id):
    """
    Replaces the ~2,200-character IT-centralization paragraph with a tally the rep can audit.
    Derived by arithmetic from the corpus, not asserted by a model.
    """
    ex_by_pid = {e["posting_id"]: e for e in extracts}
    central, local = [], []
    c_n = l_n = 0
    hq_core = site_core = 0
    for p in postings:
        if p["account_id"] != aid or p["job_family"] not in aiq.ORG_CORE:
            continue
        s = sites_by_id.get(p["site_id"], {})
        fn = (s.get("functions") or "").lower()
        if p["site_id"]:
            if "commercial" in fn or "hq" in fn:
                hq_core += 1
            else:
                site_core += 1
        e = ex_by_pid.get(p["posting_id"])
        if e:
            if e.get("autonomy_flag") == "central_owned":
                c_n += 1
            elif e.get("autonomy_flag") == "site_autonomous":
                l_n += 1
    if c_n:
        central.append("%d req(s) describe a global or central function owning the tooling" % c_n)
    if hq_core:
        central.append("%d core data/digital req(s) sit at a commercial-HQ site" % hq_core)
    if l_n:
        local.append("%d req(s) describe the site deciding its own tooling" % l_n)
    if site_core:
        local.append("%d core data/digital req(s) sit at an operating site, not HQ" % site_core)
    tot = c_n + hq_core + l_n + site_core
    if not tot:
        return ('<div class="lg"><h4>Central vs site-local</h4><div class="empty">No org-core '
                'requisitions resolved to a site yet — nothing to tally.</div></div>')
    notch = int(round(4 * ((c_n + hq_core) / float(tot))))
    return ('<div class="ledger">'
            '<div class="lg"><h4>Signs of central</h4><ul>%s</ul></div>'
            '<div class="lg"><h4>Signs of site-local</h4><ul>%s</ul></div></div>'
            '<div class="meter"><span>site</span><span class="notch">%s</span><span>central</span>'
            '<span>read from %d observation(s) — a tally, not a verdict</span></div>'
            % ("".join("<li>%s</li>" % E(x) for x in central) or '<li>none observed</li>',
               "".join("<li>%s</li>" % E(x) for x in local) or '<li>none observed</li>',
               "".join('<i class="%s"></i>' % ("on" if i <= notch else "") for i in range(5)), tot))


def lineage_timeline(aid, account, sites):
    """Replaces the ~1,500-character lineage paragraph with markers joined to the map by site."""
    marks = []
    for s in sites:
        if s["account_id"] != aid or not s.get("acquired_lineage"):
            continue
        for frag in s["acquired_lineage"].split(";"):
            m = re.match(r"\s*(.+?)\s*\((\d{4})\)\s*:?\s*(.*)", frag.strip())
            if m:
                marks.append((m.group(2), m.group(1), shorten(m.group(3), 7), s["site_id"],
                              s.get("city", "")))
    if not marks:
        b = bullets(account.get("lineage_summary", ""), maxwords=12, cap=6)
        if not b:
            return '<div class="empty">No acquisition lineage on file.</div>'
        return ('<div class="lg"><h4>Acquired-company lineage</h4><ul>%s</ul></div>'
                % "".join("<li>%s</li>" % E(x) for x in b))
    marks.sort()
    y0, y1 = int(marks[0][0]), max(int(m[0]) for m in marks)
    span = max(1, y1 - y0)
    W, H = 1040, 92
    pts = []
    for i, (yr, name, note, sid, city) in enumerate(marks):
        x = 40 + (W - 90) * (int(yr) - y0) / float(span)
        yy = 44 + (18 if i % 2 else -18)
        pts.append('<g><line x1="%.1f" y1="44" x2="%.1f" y2="%.1f" stroke="var(--border)"/>'
                   '<circle cx="%.1f" cy="44" r="4.5" fill="var(--brand)" opacity=".7"/>'
                   '<text x="%.1f" y="%.1f" font-size="10.5" fill="var(--ink)" text-anchor="middle">%s</text>'
                   '<text x="%.1f" y="%.1f" font-size="9.5" fill="var(--muted)" text-anchor="middle">%s %s</text>'
                   '<title>%s (%s) — %s · %s</title></g>'
                   % (x, x, yy, x, x, yy + (11 if i % 2 else -1), E(name[:22]),
                      x, yy + (23 if i % 2 else 11), E(yr), E(city[:16]),
                      E(name), E(yr), E(note), E(city)))
    return ('<svg viewBox="0 0 %d %d" style="width:100%%;height:auto">'
            '<line x1="30" y1="44" x2="%d" y2="44" stroke="var(--rule)"/>%s</svg>'
            % (W, H, W - 40, "".join(pts)))


# ---------------------------------------------------------------- JS
JS = r"""
<script>(function(){"use strict";
var AID=%(aid)s, BUILD=%(build)s, KEY="aiq:v1:"+AID, CURATED=%(curated)s, REP="me";
var ov=null; try{ov=JSON.parse(localStorage.getItem(KEY)||"null");}catch(e){ov=null;}
if(!ov)ov={v:1,account:AID,gen:BUILD,pins:[],verdicts:{},notes:{},queue:[],hidden:[]};
function now(){return new Date().toISOString();}
function save(){ov.updated=now();try{localStorage.setItem(KEY,JSON.stringify(ov));}
  catch(e){var p=document.querySelector(".jpanel");if(p)p.textContent="judgment layer unavailable (storage blocked) — export before leaving";}}
function eff(){var o={verdicts:{},notes:{},pins:[]};
  for(var k in (CURATED.verdicts||{}))o.verdicts[k]=CURATED.verdicts[k];
  for(var k2 in (ov.verdicts||{}))o.verdicts[k2]=ov.verdicts[k2];
  for(var k3 in (CURATED.notes||{}))o.notes[k3]=CURATED.notes[k3];
  for(var k4 in (ov.notes||{}))o.notes[k4]=ov.notes[k4];
  o.pins=(ov.pins&&ov.pins.length)?ov.pins:(CURATED.pins||[]);return o;}
function label(ref){var n=document.querySelector('[data-ref="'+ref+'"]');
  if(!n)return ref;var t=n.querySelector(".n-t, .ev-t");return t?t.textContent:ref;}
function renderPins(pins){var box=document.getElementById("pins");if(!box)return;
  var h="";pins.slice().sort(function(a,b){return (a.rank||9)-(b.rank||9);}).forEach(function(p,i){
    h+='<div class="pin"><div class="pn">'+label(p.ref)+'</div><div class="pw">'+
       (p.why?p.why:"pinned")+'</div><div class="pw"><a href="#ev-'+p.ref+'">show →</a> '+
       '<button class="jb pin-up" data-r="'+p.ref+'">↑</button>'+
       '<button class="jb pin-dn" data-r="'+p.ref+'">↓</button>'+
       '<button class="jb pin-x" data-r="'+p.ref+'">remove</button></div></div>';});
  var w=document.getElementById("pins-wrap");if(w)w.hidden=!pins.length;
  box.innerHTML=h;}
function apply(){var o=eff();
  document.querySelectorAll("[data-ref]").forEach(function(n){
    n.classList.remove("is-ok","is-no","is-pin");
    var v=o.verdicts[n.dataset.ref];
    if(v)n.classList.add(v.v==="confirm"?"is-ok":"is-no");});
  o.pins.forEach(function(p){document.querySelectorAll('[data-ref="'+p.ref+'"]')
    .forEach(function(n){n.classList.add("is-pin");});});
  renderPins(o.pins);orphanCheck(o);}
function orphanCheck(o){var box=document.getElementById("j-orphan");if(!box)return;
  var live={};document.querySelectorAll("[data-ref]").forEach(function(n){live[n.dataset.ref]=1;});
  var refs=[];for(var k in o.verdicts)if(!live[k])refs.push(k);
  (o.pins||[]).forEach(function(p){if(!live[p.ref]&&refs.indexOf(p.ref)<0)refs.push(p.ref);});
  if(!refs.length){box.hidden=true;return;}
  box.hidden=false;
  box.innerHTML='<span class="cf cf-low">'+refs.length+'</span> judgment entr'+
    (refs.length===1?"y":"ies")+' point at units no longer in this map (saved '+(ov.gen||"?")+
    ' → build '+BUILD+'). Kept by default. <button class="jb" id="o-drop">Discard them</button>';
  var b=document.getElementById("o-drop");
  if(b)b.onclick=function(){refs.forEach(function(r){delete ov.verdicts[r];
    ov.pins=(ov.pins||[]).filter(function(p){return p.ref!==r;});});save();apply();};}
function togglePin(ref){ov.pins=ov.pins||[];
  var i=-1;ov.pins.forEach(function(p,k){if(p.ref===ref)i=k;});
  if(i>=0){ov.pins.splice(i,1);}else{ov.pins.push({ref:ref,rank:ov.pins.length+1,why:"",at:now()});}}
document.addEventListener("click",function(e){
  var b=e.target.closest(".jb,.void-act");if(!b)return;
  var host=b.closest("[data-ref]");
  var ref=b.getAttribute("data-r")||(host?host.getAttribute("data-ref"):null);
  if(!ref)return;e.preventDefault();
  if(b.classList.contains("j-ok"))ov.verdicts[ref]={v:"confirm",by:REP,at:now()};
  else if(b.classList.contains("j-no"))ov.verdicts[ref]={v:"reject",by:REP,at:now()};
  else if(b.classList.contains("j-pin"))togglePin(ref);
  else if(b.classList.contains("pin-x")){ov.pins=(ov.pins||[]).filter(function(p){return p.ref!==ref;});}
  else if(b.classList.contains("pin-up")||b.classList.contains("pin-dn")){
    var d=b.classList.contains("pin-up")?-1.5:1.5;
    (ov.pins||[]).forEach(function(p){if(p.ref===ref)p.rank=(p.rank||1)+d;});}
  else return;
  save();apply();});
document.addEventListener("change",function(e){
  if(!e.target.classList||!e.target.classList.contains("jn"))return;
  var host=e.target.closest("[data-ref]");if(!host)return;
  var ref=host.getAttribute("data-ref"),t=e.target.value.trim();
  if(!t)return;(ov.notes[ref]=ov.notes[ref]||[]).push({t:t,at:now()});
  var pin=(ov.pins||[]).filter(function(p){return p.ref===ref;})[0];if(pin)pin.why=t;
  e.target.value="";save();apply();});
// deep link restores the right site lane
var m=/^#ev-([a-z0-9\-]+)/.exec(location.hash||"");
if(m){var n=document.querySelector('[data-ref="'+m[1]+'"]');
  if(n){var svg=n.closest("svg");
    if(svg){var c=(svg.getAttribute("class")||"").match(/tree-([a-z0-9\-]+)/);
      if(c){var r=document.getElementById("ln-"+c[1]);if(r)r.checked=true;}}}}
var cp=document.getElementById("j-copy");
if(cp)cp.onclick=function(){navigator.clipboard.writeText(JSON.stringify(ov,null,1)).then(
  function(){cp.textContent="Copied ✓";setTimeout(function(){cp.textContent="Copy overlay JSON";},1300);});};
var dl=document.getElementById("j-dl");
if(dl)dl.onclick=function(){var b=new Blob([JSON.stringify(ov,null,1)],{type:"application/json"});
  var a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=AID+".overlay.json";a.click();};
var mg=document.getElementById("j-merge");
if(mg)mg.onclick=function(){var t=document.getElementById("j-in");if(!t)return;
  try{var inc=JSON.parse(t.value);
    for(var k in (inc.verdicts||{}))ov.verdicts[k]=inc.verdicts[k];
    (inc.pins||[]).forEach(function(p){if(!(ov.pins||[]).some(function(q){return q.ref===p.ref;}))ov.pins.push(p);});
    save();apply();t.value="";mg.textContent="Merged ✓";}
  catch(e){mg.textContent="Not valid JSON";}};
// corpus filter (client-side over the pre-built index; does NOT query live job boards)
var IDX=null, SKIP=0;
function runFilter(){var out=document.getElementById("f-out");if(!out||!IDX)return;
  var c=(document.getElementById("f-city")||{}).value||"",
      f=(document.getElementById("f-fam")||{}).value||"",
      w=(document.getElementById("f-win")||{}).value||"",
      tool=((document.getElementById("f-tool")||{}).value||"").toLowerCase(),
      org=(document.getElementById("f-org")||{}).checked;
  var rows=IDX.filter(function(r){
    if(c&&r.site!==c)return false;
    if(f&&r.fam!==f)return false;
    if(w&&r.last<w)return false;
    if(org&&!r.ex)return false;
    if(tool&&(r.tech||"").toLowerCase().indexOf(tool)<0)return false;
    return true;});
  rows.sort(function(a,b){return b.score-a.score;});
  var h='<div class="cov"><span class="num">'+rows.length+'</span> of '+IDX.length+' requisitions match</div>';
  h+='<table><thead><tr><th>Requisition</th><th>Site</th><th>Family</th><th>Seen</th>'+
     '<th>Stack</th><th></th></tr></thead><tbody>';
  rows.slice(0,120).forEach(function(r){
    h+='<tr><td><a href="'+r.url+'" target="_blank" rel="noopener">'+r.t+'</a></td><td>'+
       (r.city||"—")+'</td><td>'+(r.fam||"—")+'</td><td class="num">'+r.last+'</td><td>'+
       (r.team?('<b>'+r.team+'</b> ')+(r.tech||''):(r.tech||'<span class="empty">—</span>'))+'</td><td>'+
       (r.ex?'<span class="cf cf-high">org</span>':'<button class="jb cpq" data-u="'+r.url+
        '">copy /org-query</button>')+'</td></tr>';});
  h+='</tbody></table>';
  if(rows.length>120)h+='<div class="cov">Showing the 120 highest-scoring of '+rows.length+
     ' — narrow the filters to see the rest.</div>';
  out.innerHTML=h;}
document.addEventListener("click",function(e){var b=e.target.closest(".cpq");if(!b)return;
  e.preventDefault();navigator.clipboard.writeText("/org-query req "+b.getAttribute("data-u"))
   .then(function(){var o=b.textContent;b.textContent="Copied ✓";
     setTimeout(function(){b.textContent=o;},1400);});});
["f-city","f-fam","f-win","f-org"].forEach(function(id){var el=document.getElementById(id);
  if(el)el.addEventListener("change",runFilter);});
var ft=document.getElementById("f-tool");if(ft)ft.addEventListener("input",runFilter);
fetch("jobs-"+AID+".json").then(function(r){return r.json();}).then(function(j){IDX=j.rows;SKIP=j.skipped_no_site||0;runFilter();})
 .catch(function(){var o=document.getElementById("f-out");
   if(o)o.innerHTML='';});
apply();
})();</script>
"""


def page(title, body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow">'
            '<title>%s</title><style>%s</style></head><body><div class="wrap">%s</div></body></html>'
            % (E(title), CSS, body))


# ---------------------------------------------------------------- account page
def render_account(a, D):
    aid = a["account_id"]
    sites = [s for s in D["sites"] if s["account_id"] == aid]
    site_by_id = {s["site_id"]: s for s in D["sites"]}
    cov = {c["site_id"]: c for c in D["org_site_coverage"] if c["account_id"] == aid}
    units = [u for u in D["org_units"] if u["account_id"] == aid and u["status"] != "rejected"]
    edges = [e for e in D["org_edges"] if e["account_id"] == aid]
    postings = [p for p in D["postings"]
                if p["account_id"] == aid or p.get("mirror_account_id") == aid]
    p_by_id = {p["posting_id"]: p for p in D["postings"]}
    extracts = [e for e in D["posting_extracts"] if e["account_id"] == aid]
    quotes = [q for q in D["posting_quotes"] if q["account_id"] == aid]
    q_by_id = {q["quote_id"]: q for q in quotes}
    people = [p for p in D["people"] if p["account_id"] == aid]
    signals = [s for s in D["signals"] if s["account_id"] == aid]

    units_by_site = defaultdict(list)
    for u in units:
        units_by_site[u["site_id"]].append(u)
    edges_by_src = defaultdict(list)
    for e in edges:
        edges_by_src[e["src_unit_id"]].append(e)
    q_by_unit = defaultdict(list)
    unit_by_slug = {u["unit_slug"]: u for u in units}
    for e in extracts:
        u = unit_by_slug.get(e.get("team_slug") or "")
        if not u:
            continue
        for q in quotes:
            if q["posting_id"] == e["posting_id"]:
                q_by_unit[u["unit_id"]].append(q)

    sup_labels = {}
    for u in units:
        s = [x["dst_label"] for x in edges_by_src.get(u["unit_id"], []) if x["edge_type"] == "supports"]
        if s:
            # Truncate by measured pixel width, not word count -- a 6-word label like "supports
            # Biopharmaceutical Development · process" still overruns a 236px card.
            sup_labels[u["unit_id"]] = orgmap.wrap(
                "supports " + " · ".join(s), 10.5, orgmap.NODE_W - 2 * orgmap.PAD_X, 1)[0]

    # A unit's parent often has no site of its own -- especially a ghost, i.e. an org named in a
    # posting but never reconstructed. Excluding those from the site's node set sends every child to
    # the unparented band and erases the hierarchy the map exists to show. So each site's tree gets
    # its own units PLUS the transitive parents of those units, wherever they live.
    units_by_id = {u["unit_id"]: u for u in units}
    parent_of = {e["src_unit_id"]: e["dst_unit_id"] for e in edges
                 if e["edge_type"] == "reports_to" and e.get("status") != "rejected"}
    for sid in list(units_by_site):
        have = {u["unit_id"] for u in units_by_site[sid]}
        frontier = list(have)
        while frontier:
            uid = frontier.pop()
            pid = parent_of.get(uid)
            if pid and pid not in have and pid in units_by_id:
                have.add(pid)
                units_by_site[sid].append(units_by_id[pid])
                frontier.append(pid)

    # ---------- spine
    idx_by_site = Counter()
    q_by_site = defaultdict(Counter)
    for p in postings:
        if p.get("site_id"):
            idx_by_site[p["site_id"]] += 1
            if p.get("period_first"):
                q_by_site[p["site_id"]][p["period_first"]] += 1
    order = sorted(sites, key=lambda s: (-idx_by_site[s["site_id"]],
                                         {"yes": 0, "soft": 1, "no": 2}.get(s.get("icp_fit"), 3),
                                         s.get("city", "")))
    mx = max(idx_by_site.values()) if idx_by_site else 1
    lanes, radios, trees = [], [], []
    first = True
    for s in order:
        sid = s["site_id"]
        rid = "ln-" + sid.replace("--", "-")
        c = cov.get(sid, {})
        state = c.get("coverage_state", "never_scanned")
        n = idx_by_site[sid]
        if state == "never_scanned":
            bar = ('<svg class="lane-bar" viewBox="0 0 56 8" aria-hidden="true">'
                   '<rect width="56" height="8" rx="4" fill="url(#hatch)"/></svg>')
            nlab = "ns"
        elif n == 0:
            bar = ('<svg class="lane-bar" viewBox="0 0 56 8" aria-hidden="true">'
                   '<rect class="bar-zero" width="3" height="8" rx="1.5"/></svg>')
            nlab = "0"
        else:
            bar = ('<svg class="lane-bar" viewBox="0 0 56 8" aria-hidden="true">'
                   '<rect class="bar" width="%.1f" height="8" rx="4"/></svg>' % max(3.0, 56.0 * n / mx))
            nlab = str(n)
        qs = sorted(q_by_site[sid])[-8:]
        radios.append('<input type="radio" name="lane" class="lin" id="%s"%s>'
                      % (rid, " checked" if first else ""))
        lanes.append('<label class="lane" for="%s"><span class="dot icp-%s"></span>'
                     '<span class="lane-c" title="%s">%s</span>%s<span class="lane-n num">%s</span>%s</label>'
                     % (rid, E(s.get("icp_fit") or "unknown"),
                        E("%s — %s" % (s.get("site", ""), s.get("city", ""))),
                        E(s.get("city") or s.get("site", "")), bar, E(nlab),
                        spark(qs, [q_by_site[sid][x] for x in qs])))
        trees.append(render_tree(s, c, units_by_site.get(sid, []),
                                [e for e in edges
                                 if e["src_unit_id"] in {u["unit_id"] for u in units_by_site.get(sid, [])}],
                                sup_labels))
        first = False
    lane_css = "".join("#ln-%s:checked~.map .tree-%s{display:block}"
                       "#ln-%s:checked~.map label[for=\"ln-%s\"]{background:var(--bg);"
                       "box-shadow:inset 3px 0 0 var(--brand)}"
                       % (s["site_id"].replace("--", "-"), s["site_id"].replace("--", "-"),
                          s["site_id"].replace("--", "-"), s["site_id"].replace("--", "-"))
                       for s in order)

    # ---------- dock
    dock = [dock_unit(u, q_by_unit, edges_by_src, site_by_id, p_by_id) for u in units]
    dock += [dock_edge(e, q_by_id, p_by_id) for e in edges if e["edge_type"] != "located_at"]
    dock.append('<aside class="ev ev-empty"></aside>')

    # ---------- coverage + gaps
    n_cov = sum(1 for c in cov.values() if c["coverage_state"] == "covered")
    n_orph = sum(1 for u in units if "no_parent" in (u["gap_flags"] or ""))
    n_ghost = sum(1 for u in units if u["unit_kind"] == "named_not_mapped")
    n_low = sum(1 for e in edges if e["confidence"] == "low")
    n_ns = sum(1 for c in cov.values() if c["coverage_state"] == "never_scanned")
    # The three toggle INPUTS are emitted separately, at .wrap level ahead of .map -- see gap_inputs
    # below. Nesting them inside .gchips (as they were) meant `#g-orphan:checked ~ .map` could never
    # match, so the chip restyled itself and looked active while the map never dimmed. Only the
    # labels live here; `for=` works at any distance.
    gapchips = "".join([
        '<label class="gchip" for="g-orphan">%d team(s) · no reporting line found</label>' % n_orph,
        '<label class="gchip" for="g-ghost">%d inferred · named in a posting only</label>' % n_ghost,
        '<label class="gchip" for="g-lowconf">%d line(s) from a single posting</label>' % n_low,
        '<span class="gchip">%d site(s) never scanned</span>' % n_ns,
    ])
    gap_inputs = ('<input type="checkbox" class="fin" id="g-orphan">'
                  '<input type="checkbox" class="fin" id="g-ghost">'
                  '<input type="checkbox" class="fin" id="g-lowconf">')

    # A "0" on a site lane is only interpretable if you know how far back the collector can see.
    # The Wayback collector carries ~2 years of history, so a zero there is a real absence of postings
    # over that window. The Workday collector can only see TODAY'S open board, so a zero there means
    # "nothing open right now" -- which at a fully-staffed manufacturing site is unremarkable and is
    # emphatically not "no organisation here". Stating the window stops the reader over-reading a zero.
    cr = next((c for c in D["careers"] if c["account_id"] == aid), {})
    kind = cr.get("pattern_kind", "")
    if kind == "workday_api":
        window = ('reads <b>today\'s open postings only</b> — this account posts through Workday, '
                  'which has no archive, so a site showing 0 has nothing open right now rather than '
                  'no organisation. History accrues from each refresh.')
    elif kind:
        window = ('reads <b>archived postings back to 2024</b>, so a site showing 0 had no '
                  'requisition captured in that window.')
    else:
        window = 'has <b>not been swept yet</b> — every site here reads 0 because nobody looked.'
    window = '<div class="cov-note">Coverage %s</div>' % window

    # Locations the collectors saw that resolve to no site of ours. Surfaced here rather than left
    # buried in city_not_in_sites, because a real missing site (Biogen's Baar) and a naming mismatch
    # look identical until someone looks.
    um = [u for u in D["unmatched_cities"] if u["account_id"] == aid and u["verdict"] == "missing_site"]
    um.sort(key=lambda u: -int(u["n"]))
    unmatched = ""
    if um:
        top = " · ".join("%s (%s)" % (E(u["city_raw"]), E(u["n"])) for u in um[:4])
        unmatched = ('<div class="cov"><span class="num">%d</span> location(s) in this account\'s '
                     'postings match no site in our map — biggest: %s</div>'
                     % (len(um), top))

    # ---------- sales-facing framing (2026-09-10)
    # The org map exists only for accounts whose requisitions were swept AND extracted. For every
    # other account the complete layers are the site map (verdicts + named heads), the Clay-verified
    # LinkedIn profiles and the publications -- so those lead, and the posting layer folds away until
    # it has something to show. Nothing is removed; the fold is the honest state.
    def vd(s):
        return (s.get("verdict_manual") or s.get("verdict_auto") or "").upper()
    n_keep = sum(1 for s in sites if vd(s) == "KEEP")
    n_named = sum(1 for s in sites if head_text(s))
    heads_by_site = defaultdict(list)
    for h in D["site_heads"]:
        if h["account_id"] == aid and h.get("found") == "true":
            for hsid in (h.get("site_ids") or "").split("|"):
                if hsid:
                    heads_by_site[hsid].append(h)
    n_linked = len({h["site_head_id"] for hs in heads_by_site.values() for h in hs})
    n_pubs = sum(1 for s in signals if s["signal_type"] == "publication")
    n_obs = len([u for u in units if u["unit_kind"] == "observed"])
    swept = bool(postings)

    J = D["jev"]
    pubrel = {s["signal_id"]: J["publication_relevance"][s["signal_id"]] for s in signals
              if s["signal_type"] == "publication" and s["signal_id"] in J["publication_relevance"]}
    n_pub_kept = sum(1 for r in pubrel.values() if r.get("keep") == "True")
    stats = chip_row([x for x in [
        a.get("pipeline_status", ""),
        ("owner · %s" % owner_of(a)) if owner_of(a) else "",
        a.get("hq", ""), a.get("est_revenue", ""),
        "%d sites · %d KEEP" % (len(sites), n_keep),
        "%d sites with a named head" % n_named,
        ("%d people verified on LinkedIn" % n_linked) if n_linked else "",
        ("%d publications · %d kept" % (n_pubs, n_pub_kept)) if pubrel else "%d publications" % n_pubs,
        ("%d indexed reqs" % len(postings)) if swept else "",
        ("%d org units" % n_obs) if n_obs else "",
    ] if x])
    # Jev account chips: digital/IT location and 12-month bioprocess hiring join the stat row; the
    # modality footprint gets its own row, label on the first chip as chip_row does.
    extra = [digital_chip(J["digital_it_location"].get(aid), site_by_id)]
    ah = J["account_hiring"].get(aid)
    if ah and inum(ah.get("n_bioprocess_core_365d")):
        extra.append(jchip(str(inum(ah["n_bioprocess_core_365d"])),
                           "%s · window from %s · newest %s" % (model_of(ah), ah.get("window_from", ""),
                                                               ah.get("newest_last_seen", "")),
                           "bioprocess reqs · 12 mo", cls="chip"))
    extra = "".join(x for x in extra if x)
    if extra and stats.startswith('<div class="chips">'):
        stats = stats[:-len("</div>")] + extra + "</div>"
    an = (J.get("account_news") or {}).get(aid)
    if an:
        try:
            top = json.loads(an.get("top") or "[]")[:4]
        except ValueError:
            top = []
        if top:
            stats += '<div class="chips">%s</div>' % "".join(news_chip(t) for t in top)
    ap = J["account_profile"].get(aid)
    mods = modality_chips(ap)
    if mods:
        stats += '<div class="chips">%s</div>' % "".join(
            jchip(m, "%s · sites with a decisive yes" % model_of(ap), "sites by modality" if i == 0 else "",
                  cls="chip") for i, m in enumerate(mods))

    curated = {"verdicts": {}, "notes": {}, "pins": (D["overrides"].get("pins") or {}).get(aid, [])}
    for uid, v in (D["overrides"].get("unit_verdicts") or {}).items():
        curated["verdicts"][uid] = v
    for eid, v in (D["overrides"].get("edge_verdicts") or {}).items():
        curated["verdicts"][eid] = v

    # DOM-ORDER CONTRACT. %(radios)s, %(gapinputs)s and #f-supports must stay siblings of .map AND
    # precede it -- the whole toggle layer is `#id:checked ~ .map` CSS with no JS. When the posting
    # layer is folded into <details>, every one of them moves inside the SAME <details> so the
    # sibling relation holds. .ev-empty must stay the last child of .dock. %(js)s must stay after
    # all markup.
    posting_block = """
<div class="cov"><span class="covbar"><i style="width:%(covpct)d%%"></i></span>
<span class="num">%(ncov)d/%(nsite)d</span> sites with reconstructed org structure</div>
%(gapinputs)s
<div class="gchips">%(gapchips)s</div>
%(unmatched)s

<div id="pins-wrap" hidden><div class="eyebrow">My pins</div><div class="pins" id="pins"></div></div>

%(radios)s
<input type="checkbox" class="fin" id="f-supports">
<div class="map">
  <svg width="0" height="0" style="position:absolute"><defs>
    <pattern id="hatch" width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="6" stroke="var(--ghost)" stroke-width="1.6"/></pattern></defs></svg>
  <nav class="spine">%(lanes)s</nav>
  <div class="canvas">%(trees)s</div>
  <div class="dock">%(dock)s</div>
</div>
<div class="legend"><label class="tog" for="f-supports">show lateral “supports” lines</label></div>

<div class="eyebrow">Query the indexed corpus</div>
<div class="chips">
  <select class="chip" id="f-city"><option value="">any site</option>%(cityopts)s</select>
  <select class="chip" id="f-fam"><option value="">any family</option>%(famopts)s</select>
  <select class="chip" id="f-win"><option value="">any time</option>
    <option value="%(y1)s">last 12 months</option><option value="%(y2)s">last 24 months</option></select>
  <input class="chip" id="f-tool" placeholder="names a tool…" style="min-width:150px">
  <label class="chip"><input type="checkbox" id="f-org"> has org structure</label>
</div>
<div id="f-out"></div>

<div class="eyebrow">Where digital / data / IT decisions look like they sit</div>
%(ledger)s
"""
    fmt = {
        "company": E(a.get("company", "")), "today": aiq.today(), "stats": stats,
        "covpct": int(100 * n_cov / max(len(sites), 1)), "ncov": n_cov, "nsite": len(sites),
        "gapchips": gapchips, "gapinputs": gap_inputs, "unmatched": unmatched,
        "window": window,
        "radios": "".join(radios), "lanes": "".join(lanes),
        "trees": "".join(trees), "dock": "".join(dock),
        "mix": hiring_mix(aid, D["posting_rollup"]),
        "ledger": central_ledger(aid, D["postings"], extracts, site_by_id),
        "lineage": lineage_timeline(aid, a, D["sites"]),
        "cityopts": "".join('<option value="%s">%s</option>' % (E(s["site_id"]), E(s.get("city") or s["site"]))
                            for s in order),
        "famopts": "".join('<option value="%s">%s</option>' % (E(f), E(f.replace("_", " ")))
                           for f in sorted({p["job_family"] for p in postings if p["job_family"]})),
        "y1": time.strftime("%Y-%m-%d", time.localtime(time.time() - 365 * 86400)),
        "y2": time.strftime("%Y-%m-%d", time.localtime(time.time() - 730 * 86400)),
        "sitetable": site_table(sites, cov, idx_by_site, units_by_site, heads_by_site, swept, J),
        "sigtable": sig_table(signals, people, pubrel),
        "js": JS % {"aid": json.dumps(aid), "build": json.dumps(aiq.today()),
                    "curated": json.dumps(curated)},
        "lanecss": lane_css,
    }
    posting_html = posting_block % fmt
    if n_obs:
        org_section = ('<div class="eyebrow">Org map</div>'
                       + posting_html)
    else:
        state = ("%d requisitions indexed" % len(postings)) if swept else "not swept yet"
        org_section = ('<details class="fold"><summary>Org map · %s</summary>%s</details>'
                       % (E(state), posting_html))
    fmt["org_section"] = org_section

    body = """
<header><h1>%(company)s</h1><span class="back"><a href="index.html">← all accounts</a></span>
<span class="back">generated %(today)s</span></header>
%(stats)s

<div class="eyebrow">Sites (%(nsite)d)</div>
%(sitetable)s
<div class="eyebrow">Publications, talks and news</div>
%(sigtable)s

%(org_section)s

<div class="eyebrow">Acquired-company lineage</div>
%(lineage)s

<details class="fold"><summary>Hiring concentration by job family</summary>
%(mix)s
<div class="jpanel"><button class="jb" id="j-copy">Copy my layer as JSON</button>
<button class="jb" id="j-dl">Download my layer</button>
<textarea id="j-in" rows="3" placeholder="paste a saved layer to merge…"></textarea>
<button class="jb" id="j-merge">Merge pasted layer</button>
<div id="j-orphan" hidden></div></div>
</details>

%(js)s
<style>%(lanecss)s</style>
""" % fmt
    return body


VERDICT_RANK = {"KEEP": 0, "FLAG": 1, "GAP": 2, "DROP": 3}


# The source sheet marks sites it never got a name for with PENDING-CLAY (156 rows), n/a, "Not
# identified", "None surfaced" and a few site-closed notes. None of those is a person.
PLACEHOLDER = re.compile(r"^(pending|tbd|n/?a\b|not identified|none surfaced|\(none|\(site clos|unknown$|-$)", re.I)


def head_text(s):
    """named_site_head as evidence text; the source sheet's placeholders read as empty."""
    t = (s.get("named_site_head") or "").strip()
    return "" if (not t or PLACEHOLDER.match(t)) else t


def owner_of(a):
    """deal_owner as recorded, with the literal nulls the source sheet uses treated as empty."""
    o = (a.get("deal_owner") or "").strip()
    return "" if o.lower() in ("", "none", "n/a", "na", "tbd", "-", "unassigned", "unknown") else o


def head_cell(s, heads):
    """Named site head. Clay-verified people link to LinkedIn; otherwise the site-map evidence text."""
    if heads:
        parts = []
        for h in heads:
            nm = E(h.get("clay_name") or h.get("name") or "")
            link = (('<a href="%s" target="_blank" rel="noopener">%s ↗</a>' % (E(h["linkedin_url"]), nm))
                    if h.get("linkedin_url") else nm)
            title = E(shorten(h.get("clay_title") or h.get("title_hint") or "", 10))
            mail = ((' · <a href="mailto:%s">%s</a>' % (E(h["email"]), E(h["email"])))
                    if h.get("email") and h.get("email_status") == "found" else "")
            parts.append('<div>%s <span class="ht">— %s</span>%s</div>' % (link, title, mail))
        how = ("found by role search" if all(h.get("source") == "clay_role_search" for h in heads)
               else "matched on LinkedIn")
        note = ('<span class="hm">%s %s</span>' % (how, E(heads[0].get("checked_at", ""))))
        return '<div class="head">%s%s</div>' % ("".join(parts), note)
    txt = head_text(s)
    if not txt:
        return '<span class="empty">—</span>'
    return '<div class="head" title="%s">%s</div>' % (E(txt), E(shorten(txt, 18)))


def site_table(sites, cov, idx_by_site, units_by_site, heads_by_site, swept, J=None):
    def vd(x):
        return (x.get("verdict_manual") or x.get("verdict_auto") or "").upper()
    J = J or {}
    fit = J.get("site_fit") or {}
    prof = J.get("site_profile") or {}
    hire = J.get("site_hiring") or {}
    fin = J.get("site_verdict_final") or {}
    snews = J.get("site_news") or {}
    sids = [s["site_id"] for s in sites]
    has_fit = any(sid in fit or sid in fin for sid in sids)
    has_news = any(sid in snews for sid in sids)
    has_prof = any(sid in prof for sid in sids)
    has_hire = any(hiring_text(hire.get(sid)) for sid in sids)
    rows = []
    for s in sorted(sites, key=lambda x: (VERDICT_RANK.get(vd(x), 4), -idx_by_site[x["site_id"]],
                                          x.get("city", ""))):
        sid = s["site_id"]
        v = vd(s)
        badge = ('<span class="cf cf-%s">%s</span>' % (E(v.lower()), E(v))) if v else ""
        cells = [
            '<td class="sitec"><span class="dot icp-%s" style="display:inline-block"></span> %s</td>'
            % (E(s.get("icp_fit") or "unknown"), E(s.get("site", ""))),
            '<td>%s</td>' % E(s.get("city", "")),
            '<td title="%s">%s</td>' % (E(s.get("icp_fit_reason") or ""), badge),
        ]
        if has_fit:
            cells.append(fit_cell(fit.get(sid), fin.get(sid)))
        cells.append('<td class="headc">%s</td>' % head_cell(s, heads_by_site.get(sid, [])))
        claude_fn = chips(s.get("functions", ""), cap=3)
        if has_prof:
            # Jev function chips replace the free-text chips; a site with no decisive function
            # keeps the site-map text so the cell is never blank for want of a label.
            cells.append(profile_chips_cell(prof.get(sid), "fn", claude_fn))
            cells.append(profile_chips_cell(prof.get(sid), "mod"))
        else:
            cells.append('<td>%s</td>' % ("".join('<span class="chip">%s</span>' % E(x)
                                                  for x in claude_fn)
                                          or '<span class="empty">—</span>'))
        if has_hire:
            # postings reach a site by city, so an office sharing a city with a plant would inherit
            # the plant's hiring (Amgen's DC government-affairs office showed "data 81"). Only
            # sites whose fit says they make or develop product carry a hiring cell.
            f = fit.get(sid) or {}
            fv = (fin.get(sid) or {}).get("verdict") or f.get("jev_verdict")
            makes = fv == "KEEP" or (fv == "FLAG" and f.get("reason_tag") in ("small-mol", "mixed", "non-core"))
            cells.append(hiring_cell(hire.get(sid)) if makes else "<td></td>")
        if has_news:
            cells.append(news_cell(snews.get(sid)))
        if swept:
            c = cov.get(sid, {})
            state = c.get("coverage_state", "never_scanned")
            cb = {"covered": '<span class="cf cf-high">covered</span>',
                  "indexed_not_extracted": '<span class="cf cf-medium">indexed only</span>',
                  "scanned_none_found": '<span class="cf cf-low">0 found</span>',
                  "never_scanned": '<span class="cf cf-low">never scanned</span>'}.get(state, "")
            cells += ['<td class="num">%d</td>' % idx_by_site[sid],
                      '<td class="num">%d</td>' % len(units_by_site.get(sid, [])),
                      '<td>%s</td>' % cb]
        rows.append("<tr>%s</tr>" % "".join(cells))
    head = ('<th>Site</th><th>City</th><th>Verdict</th>'
            + ('<th>Fit</th>' if has_fit else "")
            + '<th>Named site head</th>'
            + ('<th>Functions</th><th>Modality</th>' if has_prof else '<th>Function</th>')
            + ('<th>Hiring</th>' if has_hire else "")
            + ('<th>News</th>' if has_news else "")
            + ('<th>Reqs</th><th>Units</th><th>Coverage</th>' if swept else ""))
    return ('<div class="tw"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (head, "".join(rows)))


BAND_LABEL = {"process": "Process", "cmc": "CMC", "adjacent": "Adjacent"}


def rel_cell(r):
    """Jev relevance band (unsure / not_product hidden) + a 'Process data' chip on a decisive yes."""
    if not r:
        return "<td></td>"
    out = []
    band = BAND_LABEL.get(r.get("relevance_band") or "")
    if band:
        out.append(jchip(band, "%s · score %s · conf %s" % (model_of(r), r.get("relevance_score", ""),
                                                          r.get("relevance_conf", ""))))
    if r.get("process_data") == "yes":
        out.append(jchip("Process data", "%s · p=%s" % (model_of(r), r.get("process_data_noul", ""))))
    return '<td><div class="jc">%s</div></td>' % "".join(out) if out else "<td></td>"


def sig_table(signals, people, pubrel=None):
    """
    Publications ordered by the Jev keep flag then its in-account rank when that layer exists;
    rows below the keep bar fold away. Without the layer: the original first-12-per-kind table.
    """
    pubrel = pubrel or {}
    by = defaultdict(list)
    for s in signals:
        by[s["signal_type"]].append(s)
    order = ["publication", "conference_talk", "ma_news", "capex", "approval", "program_news"]

    def row(t, s):
        return ('<tr><td>%s</td><td>%s</td>%s<td class="num">%s</td><td>%s</td>'
                '<td>%s</td></tr>'
                % (E(t.replace("_", " ")), E(shorten(s.get("title", ""), 14)),
                   rel_cell(pubrel.get(s["signal_id"])) if pubrel else "",
                   E(s.get("date", "")), E(shorten(s.get("named_people", "").replace("|", ", "), 6)),
                   ('<a href="%s" target="_blank" rel="noopener">source ↗</a>'
                    % E(s["url"])) if s.get("url") else ""))

    def pkey(s):
        r = pubrel.get(s["signal_id"])
        if not r:
            return (1, 10 ** 6)  # not scored: after the kept rows, never folded
        return (0 if r.get("keep") == "True" else 2, inum(r.get("rank_in_account")) or 10 ** 6)

    rows, below = [], []
    for t in order:
        items = by.get(t, [])
        if t == "publication" and pubrel:
            items = sorted(items, key=pkey)
            rows += [row(t, s) for s in items if pkey(s)[0] < 2]
            below += [row(t, s) for s in items if pkey(s)[0] == 2]
        else:
            rows += [row(t, s) for s in items[:12]]
    if not rows and not below:
        return '<div class="empty">No publications, talks or news on file.</div>'
    thead = ('<thead><tr><th>Kind</th><th>Title</th>%s<th>Date</th><th>Named</th><th></th></tr></thead>'
             % ("<th>Relevance</th>" if pubrel else ""))
    main = ('<div class="tw"><table>%s<tbody>%s</tbody></table></div>' % (thead, "".join(rows))) if rows else ""
    fold = ('<details class="more"><summary>%d more publication%s</summary><div class="tw"><table>%s'
            '<tbody>%s</tbody></table></div></details>'
            % (len(below), "" if len(below) == 1 else "s", thead, "".join(below))) if below else ""
    return ('%s%s<div class="cov"><span class="num">%d</span> named people on file for this account.</div>'
            % (main, fold, len(people)))


def jobs_index(aid, D):
    """
    The pre-built corpus the in-page filter queries. No backend involved.

    Scoped to requisitions that resolved to one of this account's sites, or that were admitted for
    org extraction. The rest are overwhelmingly global commercial hiring in cities with no site in
    our spine -- for AstraZeneca that is ~3,500 of ~5,100 rows, and shipping them would put a 1.3 MB
    payload on every page load to make the filter worse. The excluded count travels with the file so
    the UI can state what it is not showing.
    """
    ex = {e["posting_id"] for e in D["posting_extracts"]}
    tech_by = {e["posting_id"]: e.get("tech_stack", "") for e in D["posting_extracts"]}
    team_by = {e["posting_id"]: e.get("team_name", "") for e in D["posting_extracts"]}
    site_city = {s["site_id"]: (s.get("city") or s.get("site", "")) for s in D["sites"]}
    rows, skipped = [], 0
    for p in D["postings"]:
        if p["account_id"] != aid and p.get("mirror_account_id") != aid:
            continue
        if not p.get("site_id") and p.get("tier2_admit") != "true":
            skipped += 1
            continue
        rows.append({"t": p["role_title"], "url": p["url"], "site": p.get("site_id", ""),
                     "city": site_city.get(p.get("site_id", ""), p.get("city_raw", "")),
                     "fam": p.get("job_family", ""), "last": p.get("last_seen", ""),
                     "score": int(p.get("tier2_score") or 0),
                     "ex": 1 if p["posting_id"] in ex else 0,
                     "team": team_by.get(p["posting_id"], ""),
                     "tech": tech_by.get(p["posting_id"], "").replace("|", " · ")})
    rows.sort(key=lambda r: -r["score"])
    return {"rows": rows, "skipped_no_site": skipped}


PIPE_GROUPS = [
    ("Live pipeline", ("Active Pipeline", "Active Prospect", "Active Partnership", "Closed Won")),
    ("No deal yet", ("No Deal Found", "")),
    ("Closed lost", ("Closed Lost",)),
]


def render_index(D, rendered):
    active = [a for a in D["accounts"] if a["active"] == "true" and a["in_universe"] == "true"]
    act = {a["account_id"] for a in active}
    units_by_a = Counter(u["account_id"] for u in D["org_units"] if u["unit_kind"] == "observed")
    posts_by_a = Counter(p["account_id"] for p in D["postings"])
    pubs_by_a = Counter(s["account_id"] for s in D["signals"] if s["signal_type"] == "publication")
    asites = [s for s in D["sites"] if s["account_id"] in act]
    sites_by_a = Counter(s["account_id"] for s in asites)
    keep_by_a = Counter(s["account_id"] for s in asites
                        if (s.get("verdict_manual") or s.get("verdict_auto") or "").upper() == "KEEP")
    named_by_a = Counter(s["account_id"] for s in asites if head_text(s))
    linked_by_a = Counter(h["account_id"] for h in D["site_heads"]
                          if h.get("found") == "true" and h["account_id"] in act)
    jfit = D["jev"]["site_fit"]
    jfit_by_a = Counter(r["account_id"] for r in jfit.values())
    jfin = D["jev"].get("site_verdict_final") or {}
    vsrc = jfin if jfin else {k: {"account_id": r["account_id"], "verdict": r.get("jev_verdict")} for k, r in jfit.items()}
    jkeep_by_a = Counter(r["account_id"] for r in vsrc.values() if r.get("verdict") == "KEEP")
    jrev_by_a = Counter(r["account_id"] for r in vsrc.values() if r.get("verdict") == "REVIEW")

    def grp(a):
        st = a.get("pipeline_status", "")
        for i, (_, statuses) in enumerate(PIPE_GROUPS):
            if st in statuses:
                return i
        return 1
    active.sort(key=lambda a: (grp(a), -keep_by_a[a["account_id"]], -units_by_a[a["account_id"]],
                               a["company"]))

    def card(a):
        aid = a["account_id"]
        ch = ['<span class="chip">%s</span>' % E(a.get("pipeline_status") or "no status on file")]
        if owner_of(a):
            ch.append('<span class="chip"><b>owner</b>%s</span>' % E(owner_of(a)))
        ch.append('<span class="chip"><b>KEEP sites</b>%d / %d</span>' % (keep_by_a[aid], sites_by_a[aid]))
        if jfit_by_a[aid]:
            ch.append('<span class="chip"><b>Fit KEEP</b>%d</span>' % jkeep_by_a[aid])
            ch.append('<span class="chip"><b>review</b>%d</span>' % jrev_by_a[aid])
        ch.append('<span class="chip"><b>sites w/ named head</b>%d</span>' % named_by_a[aid])
        if linked_by_a[aid]:
            ch.append('<span class="chip"><b>people on LinkedIn</b>%d</span>' % linked_by_a[aid])
        ch.append('<span class="chip"><b>pubs</b>%d</span>' % pubs_by_a[aid])
        if posts_by_a[aid]:
            ch.append('<span class="chip"><b>reqs</b>%d</span>' % posts_by_a[aid])
        if units_by_a[aid]:
            ch.append('<span class="chip"><b>org units</b>%d</span>' % units_by_a[aid])
        return ('<a class="lg" href="%s.html" style="display:block">'
                '<div style="font-size:15px;font-weight:600;color:var(--ink)">%s</div>'
                '<div class="chips">%s</div></a>' % (E(aid), E(a["company"]), "".join(ch)))

    groups = []
    for i, (label, _) in enumerate(PIPE_GROUPS):
        members = [a for a in active if grp(a) == i]
        if not members:
            continue
        groups.append('<div class="grp"><div class="eyebrow">%s (%d)</div>'
                      '<div class="ledger" style="grid-template-columns:repeat(auto-fill,minmax(290px,1fr))">'
                      '%s</div></div>' % (E(label), len(members), "".join(card(a) for a in members)))

    n_swept = sum(1 for a in active if posts_by_a[a["account_id"]])
    n_mapped = sum(1 for a in active if units_by_a[a["account_id"]])
    body = ('<header><h1>Account intelligence</h1>'
            '<span class="back">generated %s</span></header>'
            '<div class="cov-note"><span class="num">%d</span> active accounts · '
            '<span class="num">%d</span> sites, <span class="num">%d</span> KEEP · '
            '<span class="num">%d</span> sites with a named head, <span class="num">%d</span> people matched on LinkedIn · '
            '<span class="num">%d</span> publications</div>'
            '%s'
            % (aiq.today(), len(active), len(asites), sum(keep_by_a.values()),
               sum(named_by_a.values()), sum(linked_by_a.values()), sum(pubs_by_a.values()),
               "".join(groups)))
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(
        page("Account intelligence — Invert Bio", body))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", action="append")
    ap.add_argument("--allow-prose", action="store_true", help="skip the no-paragraph assertion")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    D = load_all()
    n = 0
    for acc in D["accounts"]:
        if acc["active"] != "true" or acc["in_universe"] != "true":
            continue
        if a.account and acc["account_id"] not in a.account:
            continue
        body = render_account(acc, D)
        doc = page("%s — account intelligence" % acc["company"], body)

        # THE PROSE ASSERTION. A paragraph in the output means a regression in the rules above.
        if not a.allow_prose:
            if re.search(r"<p[ >]", doc):
                raise ProseError("PROSE GUARD: %s emitted a <p> tag. The brief must be scannable "
                                 "slots, not paragraphs." % acc["account_id"])
            # Every blockquote must sit inside an <ol class="ev-src"> region: prose is admissible as
            # quoted evidence and nowhere else. Compare positions against the actual regions rather
            # than a fixed lookback, which mis-fires on the 2nd and later <li> of a long list.
            spans = []
            for m in re.finditer(r'<ol class="ev-src">', doc):
                end = doc.find("</ol>", m.end())
                spans.append((m.start(), end if end > 0 else len(doc)))
            for m in re.finditer(r"<blockquote", doc):
                if not any(s <= m.start() <= e for s, e in spans):
                    raise ProseError("PROSE GUARD: %s has a <blockquote> outside .ev-src at offset "
                                     "%d -- prose is admissible as evidence only."
                                     % (acc["account_id"], m.start()))
        open(os.path.join(OUT, "%s.html" % acc["account_id"]), "w", encoding="utf-8").write(doc)
        json.dump(jobs_index(acc["account_id"], D),
                  open(os.path.join(OUT, "jobs-%s.json" % acc["account_id"]), "w"),
                  separators=(",", ":"))
        n += 1
    render_index(D, n)
    print("rendered %d brief(s) + index.html -> %s" % (n, OUT))
    print("prose guard: SKIPPED (--allow-prose)" if a.allow_prose
          else "prose guard: passed (no <p>, no blockquote outside .ev-src)")


if __name__ == "__main__":
    main()

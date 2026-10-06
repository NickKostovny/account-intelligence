#!/usr/bin/env python3
"""
The spatial org map: deterministic layout in Python, baked into self-contained SVG.

Why the layout is computed here and not in the browser: the output has to be stable and diffable
(same data in, byte-identical SVG out), self-contained (no CDN, no layout library), and readable
with JavaScript switched off. Every sort key is a total order for that reason.

How "spatial" and "scannable" are reconciled -- they pull opposite ways, and the resolution is
structural, not cosmetic:
  * The SPINE is one DOM that reads two ways: a ranked list you scan top-to-bottom like bullets,
    and a bar chart you read by length. Same element, both modes, no prose.
  * The CANVAS is spatial at the overview level; its leaf cards are FIXED-SLOT, not free text. A
    card has five slots and cannot grow a sixth. Overflow goes to the dock or is dropped.
  * Prose is admissible as EVIDENCE only -- a blockquote of somebody else's words, dated and
    linked -- never as conclusion.

Gaps consume layout space. A named-but-unmapped parent gets a column, a y slot, and pushes real
siblings down; an unscanned site gets a hatched full-length bar, because an absent bar reads as
"small", which would be a lie.
"""
import math, re
from collections import defaultdict

# ---------------------------------------------------------------- geometry
MARGIN_X, MARGIN_Y = 20, 18
COL_W = 268          # x pitch between depth levels
NODE_W = 236         # card width; the 32px gutter carries the elbow trunk
PAD_X, PAD_Y = 12, 14
TRUNK = 16
CORNER = 10
BAND_GAP = 26
CONF_RANK = {"human": 4, "high": 3, "medium": 2, "low": 1, "": 0}


def X(d):
    return MARGIN_X + d * COL_W


# ---------------------------------------------------------------- text metrics
# SVG has no auto-wrap, so wrapping happens here. A 4-bucket advance-width estimator tuned for
# DM Sans, padded 4% for the system-ui fallback so text never overruns its card.
_NARROW = set("iljtfrI.,:;'|!()[]")
_WIDE = set("mwMWQ@%&")


def tw(s, px):
    w = 0.0
    for c in s:
        if c in _NARROW:
            w += 0.30
        elif c in _WIDE:
            w += 0.92
        elif c.isupper() or c.isdigit():
            w += 0.62
        else:
            w += 0.545
    return w * px * 1.04


def wrap(s, px, maxw, maxlines):
    out, cur = [], ""
    for word in (s or "").split():
        cand = (cur + " " + word).strip()
        if tw(cand, px) <= maxw or not cur:
            cur = cand
        else:
            out.append(cur)
            cur = word
            if len(out) == maxlines:
                break
    if cur and len(out) < maxlines:
        out.append(cur)
    if not out:
        return [""]
    if len(out) == maxlines and tw(out[-1], px) > maxw * 0.94:
        while out[-1] and tw(out[-1] + "…", px) > maxw:
            out[-1] = out[-1][:-1]
        out[-1] += "…"
    return out


def pack_chips(items, maxw, px=10):
    """Greedy chip rows. Never returns [] -- an empty stack must still occupy a row so the gap is
    visible rather than shrinking the card."""
    if not items:
        items = ["stack ?"]
    rows, cur, curw = [], [], 0.0
    for it in items:
        w = tw(it, px) + 16 + 6
        if cur and curw + w > maxw:
            rows.append(cur)
            cur, curw = [it], w
        else:
            cur.append(it)
            curw += w
        if len(rows) == 2:
            break
    if cur and len(rows) < 2:
        rows.append(cur)
    return rows[:2]


# ---------------------------------------------------------------- node model
class N(object):
    def __init__(self, unit):
        self.u = unit
        self.uid = unit["unit_id"]
        self.name = unit["unit_name_display"]
        self.acronym = unit.get("acronym") or ""
        self.ghost = unit.get("unit_kind") == "named_not_mapped"
        self.conf = unit.get("confidence") or ""
        self.gaps = [g for g in (unit.get("gap_flags") or "").split("|") if g]
        self.tech = [t for t in (unit.get("tech_stack") or "").split("|") if t][:6]
        self.supports = [s for s in (unit.get("aliases") or "").split("|") if s]
        try:
            self.n = int(unit.get("n_postings") or 0)
        except ValueError:
            self.n = 0
        self.children = []
        self.parent_conflict = False   # set when a reporting-line cycle had to be broken
        self.x = self.y = 0
        self.h = 56

    def measure(self, supports_label=""):
        self.title_lines = wrap(self.name, 13.5, NODE_W - 2 * PAD_X, 2)
        self.sup_label = supports_label
        self.chip_rows = [] if self.ghost else pack_chips(self.tech, NODE_W - 2 * PAD_X)
        if self.ghost:
            self.h = max(58, 12 + 17 * len(self.title_lines) + 16 + 8)
            return self.h
        h = (12 + 17 * len(self.title_lines)
             + (13 if self.acronym else 0)
             + (15 if self.sup_label else 0)
             + 18 * len(self.chip_rows)
             + (12 if self.parent_conflict else 0)
             + 10 + 6)
        self.h = max(58, 2 * int(math.ceil(h / 2.0)))
        return self.h


def build_forest(units, edges):
    """
    -> (roots, orphans). Roots are trees reachable by reports_to; orphans are observed units with no
    observed parent -- they go in their own labelled band rather than being grafted onto a fake
    hierarchy.
    """
    nodes = {u["unit_id"]: N(u) for u in units}
    parent_of, weight = {}, {}
    for e in edges:
        if e["edge_type"] == "reports_to" and e.get("status") != "rejected":
            if e["src_unit_id"] in nodes and e["dst_unit_id"] in nodes:
                if e["src_unit_id"] == e["dst_unit_id"]:
                    continue
                parent_of.setdefault(e["src_unit_id"], e["dst_unit_id"])
                try:
                    n_p = int(e.get("n_postings") or 0)
                except ValueError:
                    n_p = 0
                weight[e["src_unit_id"]] = (n_p, CONF_RANK.get(e.get("confidence"), 0))

    # Break reporting-line CYCLES. Two postings can each name the other team as the parent -- Biogen
    # has exactly that between "Omnichannel Excellence" and "North American Business Operations &
    # Insights". Both nodes then have a parent, so neither is a root and neither is an orphan, and
    # both silently disappeared off the map: the header said 7 teams and 5 rendered.
    #
    # Break at the weakest-evidenced link (fewest postings, then lowest confidence, then id for
    # determinism) and MARK the node, because a conflicting reporting line is a real finding about
    # the source data, not something to quietly resolve.
    for start in list(parent_of):
        seen, cur, chain = set(), start, []
        while cur in parent_of:
            if cur in seen:
                loop = chain[chain.index(cur):] if cur in chain else chain
                victim = min(loop, key=lambda u: (weight.get(u, (0, 0)), u))
                parent_of.pop(victim, None)
                nodes[victim].parent_conflict = True
                break
            seen.add(cur)
            chain.append(cur)
            cur = parent_of[cur]

    for uid, pid in parent_of.items():
        nodes[pid].children.append(nodes[uid])
    has_parent = set(parent_of)
    roots, orphans = [], []
    for uid, n in nodes.items():
        if uid in has_parent:
            continue
        if n.children:
            roots.append(n)
        elif n.ghost:
            continue                      # a childless ghost carries no information
        else:
            orphans.append(n)
    roots.sort(key=lambda n: (-n.n, -CONF_RANK.get(n.conf, 0), n.name, n.uid))
    orphans.sort(key=lambda n: (-n.n, -CONF_RANK.get(n.conf, 0), n.name, n.uid))
    return roots, orphans, nodes


def tidy(node, depth, cur, level_max, sup_labels):
    node.x = X(depth)
    node.measure(sup_labels.get(node.uid, ""))
    kids = sorted(node.children, key=lambda c: (-c.n, -CONF_RANK.get(c.conf, 0), c.name, c.uid))
    if not kids:
        node.y = max(cur[0], level_max.get(depth, MARGIN_Y))
    else:
        centres = [tidy(c, depth + 1, cur, level_max, sup_labels) for c in kids]
        node.y = (min(centres) + max(centres)) / 2.0 - node.h / 2.0
        node.y = max(node.y, level_max.get(depth, MARGIN_Y))
    level_max[depth] = node.y + node.h + PAD_Y
    cur[0] = max(cur[0], level_max[depth])
    return node.y + node.h / 2.0


def unsqueeze(roots):
    """Deepest-first overlap resolution. Pushes only ever move down, so it converges fast."""
    by_depth = defaultdict(list)

    def walk(n):
        by_depth[int(round((n.x - MARGIN_X) / COL_W))].append(n)
        for c in n.children:
            walk(c)
    for r in roots:
        walk(r)

    def shift(n, dy):
        n.y += dy
        for c in n.children:
            shift(c, dy)

    for _ in range(8):
        moved = False
        for d in sorted(by_depth, reverse=True):
            row = sorted(by_depth[d], key=lambda n: n.y)
            for i in range(1, len(row)):
                need = row[i - 1].y + row[i - 1].h + PAD_Y
                if row[i].y < need - 0.5:
                    shift(row[i], need - row[i].y)
                    moved = True
        if not moved:
            break
    return by_depth


def elbow(px, py, ph, cx, cy):
    x0, x1 = px + NODE_W, cx
    y0, y1 = py + ph / 2.0, cy
    if abs(y1 - y0) < 1:
        return "M%.1f,%.1f H%.1f" % (x0, y0, x1)
    xm = x0 + TRUNK
    s = 1 if y1 > y0 else -1
    r = min(CORNER, abs(y1 - y0) / 2.0)
    return ("M%.1f,%.1f H%.1f Q%.1f,%.1f %.1f,%.1f V%.1f Q%.1f,%.1f %.1f,%.1f H%.1f"
            % (x0, y0, xm - r, xm, y0, xm, y0 + s * r, y1 - s * r, xm, y1, xm + r, y1, x1))


# ---------------------------------------------------------------- CSS
CSS = r"""
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f2f5ee;--surface:#fff;--border:#e5e9e8;--ink:#223436;--brand:#1d363b;--body:#555d5a;
 --muted:#737c79;--label:#9ca2a0;--link:#2a5a5e;--ok:#3f7d63;--pending:#b6bcb9;--sched:#b07b2c;
 --done:#2a5a5e;--neg:#a6512f;--ghost:#b6bcb9;--rule:#cfd6d2;
 --sans:'DM Sans',ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
@media(prefers-color-scheme:dark){:root{--bg:#101e21;--surface:#17282b;--border:#294044;
 --ink:#eaf2ec;--brand:#eaf2ec;--body:#b7c4c0;--muted:#8a9894;--label:#7d8c88;--link:#8fc8bf;
 --ok:#7fc7a6;--pending:#5c6b68;--sched:#d0a24e;--done:#8fc8bf;--neg:#e2926b;--ghost:#4a5f62;
 --rule:#35494d}}
html,body{background:var(--bg);color:var(--body);font-family:var(--sans);-webkit-font-smoothing:antialiased}
body{padding:0 20px 70px;line-height:1.55;font-size:14px}
.wrap{max-width:1400px;margin:0 auto}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
.num,.d,.lane-n,.bar-n,.cf,.pill{font-variant-numeric:tabular-nums}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;padding:24px 0 4px}
h1{font-weight:400;font-size:clamp(24px,3.2vw,34px);letter-spacing:-.02em;color:var(--ink)}
.back{font-size:12.5px;color:var(--muted)}
.eyebrow{font-size:11px;font-weight:500;letter-spacing:.12em;text-transform:uppercase;
 color:var(--label);margin:26px 0 8px;display:flex;align-items:center;gap:10px}
.eyebrow::before{content:"";width:20px;height:1px;background:var(--label)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{background:var(--surface);border:1px solid var(--border);border-radius:999px;
 padding:3px 10px;font-size:12px;white-space:nowrap;color:var(--body)}
.chip b{color:var(--label);font-weight:600;font-size:10.5px;letter-spacing:.04em;
 text-transform:uppercase;margin-right:5px}
.cf{font-size:10px;font-weight:700;letter-spacing:.04em;padding:1px 7px;border-radius:5px;color:#fff}
.cf-high{background:var(--ok)}.cf-medium{background:var(--sched)}.cf-low{background:var(--neg)}
.cf-human{background:var(--done)}
.cov{display:flex;align-items:center;gap:8px;margin:12px 0 2px;font-size:12px;color:var(--muted)}
.cov-note{display:block;margin:10px 0 2px;font-size:12px;color:var(--muted);max-width:96ch;
 line-height:1.5}
.cov-note b{color:var(--ink);font-weight:600}
.covbar{width:190px;height:7px;border-radius:4px;background:var(--border);overflow:hidden}
.covbar i{display:block;height:100%;background:var(--ok)}
.gchips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}
.gchip{font-size:11.5px;padding:3px 9px;border-radius:999px;border:1px dashed var(--ghost);
 color:var(--muted);cursor:pointer;user-select:none;background:transparent}
.fin:checked+.gchip{border-style:solid;border-color:var(--neg);color:var(--neg);font-weight:600}
.lin,.zin,.fin{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
.map{display:grid;grid-template-columns:330px minmax(0,1fr) 336px;gap:16px;align-items:start;
 margin-top:12px}
@media(max-width:1180px){.map{grid-template-columns:280px minmax(0,1fr)}.dock{grid-column:1/-1}}
@media(max-width:820px){.map{grid-template-columns:1fr}}
.spine{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:6px;
 max-height:76vh;overflow:auto}
.lane{display:grid;grid-template-columns:9px 1fr 56px 24px 40px;gap:6px;align-items:center;
 padding:6px 8px;border-radius:8px;cursor:pointer;font-size:12.5px;color:var(--body)}
.lane:hover{background:var(--bg)}
.lane-c{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lane-n{text-align:right;color:var(--muted);font-size:11.5px}
.dot{width:9px;height:9px;border-radius:50%;display:block}
.icp-yes{background:var(--ok)}.icp-soft{background:var(--sched)}
.icp-no{background:var(--pending)}.icp-unknown{background:var(--ghost)}
.bar{fill:var(--brand);opacity:.5}.bar-zero{fill:var(--pending)}
.spark rect{fill:var(--muted);opacity:.4}.spark rect.hot{fill:var(--sched);opacity:1}
.canvas{background:var(--surface);border:1px solid var(--border);border-radius:12px;
 max-height:76vh;overflow:auto;padding:4px}
.tree{display:none;height:auto}
.legend{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:10px 0 0;font-size:11.5px;
 color:var(--muted)}
.legend b{color:var(--label);font-weight:600;font-size:10.5px;letter-spacing:.06em;
 text-transform:uppercase}
.lgi{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.lgk{display:inline-block;width:22px;height:0;border-top:2px solid var(--muted);flex:none}
.lgk-dot{border-top-style:dotted;border-color:var(--pending)}
.lgk-dash{border-top-style:dashed;border-color:var(--sched)}
.lgk-box{width:16px;height:11px;border:1px dashed var(--ghost);border-top-width:1px}
.lgk-hatch{width:22px;height:8px;border:0;border-radius:4px;
 background:repeating-linear-gradient(45deg,transparent,transparent 3px,var(--ghost) 3px,var(--ghost) 4px)}
.tog{padding:3px 9px;border:1px solid var(--border);border-radius:999px;cursor:pointer;
 background:var(--surface);color:var(--body);user-select:none}
#f-supports:checked~.map+.legend .tog,#f-supports:checked~.legend .tog{border-color:var(--sched);
 color:var(--sched);font-weight:600}
svg text{font-family:var(--sans)}
.n-bg{fill:var(--surface);stroke:var(--border)}
.nd:hover .n-bg{stroke:var(--link)}
.nd:focus-visible .n-bg{stroke:var(--link);stroke-width:2}
.n-t{font-size:13.5px;fill:var(--ink)}
.n-ac{font-size:10.5px;font-weight:700;letter-spacing:.06em;fill:var(--label)}
.n-sup{font-size:10.5px;fill:var(--muted)}
.n-power{fill:var(--brand);opacity:.35}
.chip-g rect{fill:var(--bg);stroke:var(--border)}
.chip-g text{font-size:10px;fill:var(--body)}
.chip-void rect{fill:none;stroke:var(--ghost);stroke-dasharray:3 3}
.chip-void text{fill:var(--label)}
.n-ghost .n-bg{fill:none;stroke:var(--ghost);stroke-dasharray:4 4}
.n-ghost .n-t{fill:var(--muted);font-style:italic}
.n-tag{font-size:9px;letter-spacing:.09em;fill:var(--label)}
.n-conf{font-size:8.5px;letter-spacing:.07em;fill:var(--neg);font-weight:700}
.n-q{font-size:19px;font-weight:600;fill:var(--ghost);text-anchor:end}
.e-line{fill:none;stroke:var(--muted);stroke-linecap:round}
.e-high .e-line{stroke-width:2.2}
.e-medium .e-line{stroke-width:1.5;stroke-dasharray:4 3;opacity:.75}
.e-low .e-line{stroke-width:1;stroke-dasharray:2 5;opacity:.5}
.e-human .e-line{stroke:var(--ok);stroke-width:2.4;opacity:1}
.e-located .e-line{stroke:var(--pending);stroke-dasharray:1 4;stroke-width:1}
.e-supports{opacity:0;pointer-events:none}
#f-supports:checked~.map .e-supports{opacity:.5;pointer-events:auto}
.e-supports .e-line{stroke:var(--sched);stroke-dasharray:5 4;fill:none}
.hit{fill:none;stroke:transparent;stroke-width:16}
.e-pill rect{fill:var(--surface);stroke:var(--border)}
.e-pill text{font-size:9px;fill:var(--muted);text-anchor:middle}
.e-low .e-pill rect{stroke:var(--neg)}
.e-low .e-pill text{fill:var(--neg);font-weight:700}
.siteroot rect{fill:var(--bg);stroke:var(--border)}
.siteroot text{font-size:11px;font-weight:600;letter-spacing:.06em;fill:var(--muted)}
.band-rule{stroke:var(--neg);stroke-dasharray:6 5;stroke-width:1;opacity:.5}
.band-lbl{font-size:9px;letter-spacing:.09em;fill:var(--neg);opacity:.85}
.stub-dead{stroke:var(--neg);stroke-dasharray:3 3;stroke-width:1.2;fill:none}
.stub-cap{fill:none;stroke:var(--neg);stroke-width:1.2}
.void-fr{fill:none;stroke:var(--ghost);stroke-dasharray:5 5}
.void-h{font-size:11px;font-weight:600;letter-spacing:.08em;fill:var(--label)}
.void-b{font-size:12px;fill:var(--muted)}
.dock{position:sticky;top:14px}
.ev{display:none}
.ev:target{display:block}
.ev-empty{display:block}
.ev:target~.ev-empty{display:none}
.evbox{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 15px}
.evbox-hint{font-size:12.5px;color:var(--muted);border-style:dashed;background:transparent}
.conf{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;margin:6px 0 2px}
.conf-b{font-size:11.5px;color:var(--muted)}
.more{margin-top:6px}
.more summary{font-size:11.5px;color:var(--link);cursor:pointer;padding:5px 0;list-style:none}
.more summary::-webkit-details-marker{display:none}
.more summary::before{content:"+ ";font-weight:700}
.more[open] summary::before{content:"\2212 "}
.ev-h{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.ev-kind{font-size:9.5px;letter-spacing:.1em;color:var(--label);font-weight:700}
.ev-x{margin-left:auto;color:var(--muted);font-size:14px;line-height:1}
.ev-t{font-size:15px;font-weight:600;color:var(--ink);margin-bottom:2px}
.ev-t .ac{font-size:11px;font-weight:700;color:var(--label);letter-spacing:.06em;margin-left:5px}
.ev-facts{list-style:none;margin:10px 0 0;font-size:12.5px}
.ev-facts li{display:flex;gap:8px;padding:3px 0;border-bottom:1px solid var(--border)}
.ev-facts li:last-child{border-bottom:0}
.ev-facts .k{color:var(--label);min-width:88px;font-size:11px;text-transform:uppercase;
 letter-spacing:.05em;padding-top:2px}
.ev-facts .v{color:var(--body);flex:1}
.ev-facts .miss .v{color:var(--label);font-style:italic}
.ev-facts .miss .v::after{content:" \2310";color:var(--neg)}
.ev-src{list-style:none;margin:12px 0 0;counter-reset:s}
.ev-src li{padding:8px 0;border-top:1px solid var(--border)}
.src-h{display:flex;gap:8px;align-items:center;font-size:11px;color:var(--muted)}
.src-t{font-size:12.5px;color:var(--ink);margin:3px 0 0}
.vb-l,.inf-l{font-size:9px;letter-spacing:.09em;color:var(--label);margin-top:7px;font-weight:700}
.vb{font-size:12.5px;line-height:1.5;color:var(--body);border-left:2px solid var(--border);
 padding:2px 0 2px 9px;margin:3px 0 0;font-style:normal}
.vb.unver{border-left-color:var(--neg)}
.ev-inf{border:1px dashed var(--ghost);border-radius:8px;padding:8px 10px;margin-top:10px}
.ev-inf ul{list-style:none;font-size:12px;color:var(--muted)}
.ev-j{display:flex;flex-wrap:wrap;gap:5px;margin-top:12px;padding-top:10px;
 border-top:1px solid var(--border)}
.jb{font-size:11.5px;padding:4px 10px;border:1px solid var(--border);border-radius:6px;
 background:var(--bg);color:var(--body);cursor:pointer;font-family:var(--sans)}
.jb:hover{border-color:var(--link);color:var(--link)}
.jn{flex:1 1 100%;font-size:12px;padding:5px 8px;border:1px solid var(--border);border-radius:6px;
 background:var(--bg);color:var(--body);font-family:var(--sans)}
.nd.is-ok .n-bg{stroke:var(--ok);stroke-width:2}
.nd.is-no{opacity:.34}
.nd.is-no .n-t{text-decoration:line-through}
.nd.is-pin .n-bg{stroke:var(--sched);stroke-width:2}
.nd .n-pin{display:none}
.nd.is-pin .n-pin{display:block}
.n-pin{fill:var(--sched)}
.eg.is-ok .e-line{stroke:var(--ok);stroke-dasharray:none;opacity:1}
.eg.is-no .e-line{stroke:var(--neg);opacity:.3}
.pins{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 0}
.pin{background:var(--surface);border:1px solid var(--sched);border-radius:9px;padding:7px 10px;
 font-size:12px;min-width:150px}
.pin .pn{font-weight:600;color:var(--ink)}
.pin .pw{color:var(--muted);font-size:11.5px;margin-top:2px}
.pin-empty{font-size:12.5px;color:var(--muted)}
.jpanel{margin-top:14px;padding-top:12px;border-top:1px solid var(--border);display:flex;gap:6px;
 flex-wrap:wrap;align-items:center;font-size:12px;color:var(--muted)}
.jpanel textarea{flex:1 1 100%;font-family:var(--sans);font-size:12px;padding:6px 8px;
 border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--body)}
.fold{margin-top:26px;border-top:1px solid var(--border);padding-top:10px}
.fold>summary{font-size:11px;font-weight:500;letter-spacing:.12em;text-transform:uppercase;
 color:var(--label);cursor:pointer;padding:6px 0;list-style:none}
.fold>summary::-webkit-details-marker{display:none}
.fold>summary::before{content:"+ ";font-weight:700}
.fold[open]>summary::before{content:"\2212 "}
/* These require the #g-* inputs to be siblings of .map at .wrap level and to precede it. They
   used to be nested inside .gchips, so the chip restyled itself and looked active while the map
   never dimmed. Do not move the inputs back into the chip row. */
#g-orphan:checked~.map .nd:not(.n-orphan):not(.n-ghost){opacity:.2}
#g-orphan:checked~.map .eg{opacity:.1}
#g-ghost:checked~.map .nd:not(.n-ghost){opacity:.2}
#g-lowconf:checked~.map .eg:not(.e-low){opacity:.12}
#g-lowconf:checked~.map .nd{opacity:.35}
#f-supports:checked~.legend .tog{border-color:var(--sched);color:var(--sched);font-weight:600}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--border);font-size:12px;
 color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--label);font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em}
.ledger{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:8px}
@media(max-width:760px){.ledger{grid-template-columns:1fr}}
.lg{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 13px}
.lg h4{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--label);
 font-weight:700;margin-bottom:7px}
.lg ul{list-style:none;font-size:12.5px}
.lg li{padding:3px 0;display:flex;gap:7px}
.lg li::before{content:"\2022";color:var(--ghost)}
.meter{display:flex;align-items:center;gap:7px;margin-top:10px;font-size:11.5px;color:var(--muted)}
.notch{display:flex;gap:3px}
.notch i{width:15px;height:5px;border-radius:3px;background:var(--border)}
.notch i.on{background:var(--brand)}
.hatch-cell{background:repeating-linear-gradient(45deg,transparent,transparent 3px,
 var(--ghost) 3px,var(--ghost) 4px)}
.empty{color:var(--label);font-style:italic;font-size:12.5px}
"""


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ---------------------------------------------------------------- SVG emitters
def node_svg(n, cls_extra=""):
    """A fixed-slot card. Five slots, never six."""
    g = []
    kind = "n-ghost" if n.ghost else "n-real"
    cls = "nd %s %s" % (kind, cls_extra)
    g.append('<a class="%s" href="#ev-%s" data-ref="%s" tabindex="0">'
             % (cls, n.uid, n.uid))
    g.append('<rect class="n-bg" x="%d" y="%.1f" width="%d" height="%.1f" rx="8"/>'
             % (n.x, n.y, NODE_W, n.h))
    ty = n.y + 12 + 13
    for line in n.title_lines:
        g.append('<text class="n-t" x="%d" y="%.1f">%s</text>' % (n.x + PAD_X, ty, esc(line)))
        ty += 17
    if n.ghost:
        g.append('<text class="n-tag" x="%d" y="%.1f">INFERRED — NAMED IN A POSTING ONLY</text>'
                 % (n.x + PAD_X, ty + 1))
        g.append('<text class="n-q" x="%d" y="%.1f">?</text>' % (n.x + NODE_W - 10, n.y + n.h - 12))
        g.append('<title>%s\nInferred: a posting named this org, but it has no requisitions of '
                 'its own so nothing else about it is known. Click for the quoted line.</title>'
                 % esc(n.name))
    else:
        if n.acronym:
            g.append('<text class="n-ac" x="%d" y="%.1f">%s</text>'
                     % (n.x + PAD_X, ty + 1, esc(n.acronym)))
            ty += 13
        if n.sup_label:
            g.append('<text class="n-sup" x="%d" y="%.1f">%s</text>'
                     % (n.x + PAD_X, ty + 2, esc(n.sup_label)))
            ty += 15
        cy = ty + 4
        for row in n.chip_rows:
            cx = n.x + PAD_X
            for it in row:
                w = int(tw(it, 10) + 16)
                void = "chip-void" if it == "stack ?" else ""
                g.append('<g class="chip-g %s" transform="translate(%d,%.1f)">'
                         '<rect width="%d" height="15" rx="7.5"/>'
                         '<text x="8" y="11">%s</text></g>' % (void, cx, cy, w, esc(it)))
                cx += w + 6
            cy += 18
        # power bar: relative posting volume, the "busiest team" read
        if n.n > 0:
            g.append('<rect class="n-power" x="%d" y="%.1f" width="%.1f" height="4" rx="2"/>'
                     % (n.x, n.y + n.h - 5, min(NODE_W, 26 + 22 * min(n.n, 9))))
        g.append('<circle class="n-pin" cx="%d" cy="%.1f" r="4"/>' % (n.x + NODE_W - 12, n.y + 12))
        if n.parent_conflict:
            g.append('<text class="n-conf" x="%d" y="%.1f">\u27f3 POSTINGS DISAGREE ON REPORTING '
                     'LINE</text>' % (n.x + PAD_X, cy + 2))
        g.append('<title>%s -- %d posting%s, %s confidence%s</title>'
                 % (esc(n.name), n.n, "" if n.n == 1 else "s", esc(n.conf or "unrated"),
                    ("\nPostings disagree about who this team reports to; the weaker-evidenced "
                     "line was set aside. Click for the quoted lines." if n.parent_conflict else "")))
    g.append('<rect class="hit" x="%d" y="%.1f" width="%d" height="%.1f" fill="transparent"/>'
             % (n.x - 6, n.y - 6, NODE_W + 12, n.h + 12))
    g.append("</a>")
    return "".join(g)


def edge_svg(e, src, dst, path, cls):
    conf = e.get("confidence") or "low"
    if e.get("human_state") == "confirm":
        conf = "human"
    n_p = e.get("n_postings") or "1"
    g = ['<a class="eg %s e-%s" href="#ev-%s" data-ref="%s">' % (cls, conf, e["edge_id"], e["edge_id"])]
    g.append('<path class="e-line" d="%s"/>' % path)
    if cls == "e-parent":
        m = re.search(r"H([-\d.]+) Q([-\d.]+),([-\d.]+)", path)
        if m:
            xm = float(m.group(2))
            ymid = (src.y + src.h / 2.0 + dst.y + dst.h / 2.0) / 2.0
            g.append('<g class="e-pill" transform="translate(%.1f,%.1f)">'
                     '<rect x="-11" y="-8" width="22" height="16" rx="8"/>'
                     '<text x="0" y="4">%s×</text></g>' % (xm, ymid, esc(n_p)))
    g.append('<title>%s · %s confidence · %s observation%s</title>'
             % (esc(e["edge_type"]), esc(conf), esc(n_p), "" if n_p == "1" else "s"))
    g.append('<path class="hit" d="%s"/>' % path)
    g.append("</a>")
    return "".join(g)


def site_root_bar(site_label, n_postings, width):
    return ('<g class="siteroot"><rect x="%d" y="%d" width="%d" height="26" rx="7"/>'
            '<text x="%d" y="%d">SITE · %s · %d POSTING%s INDEXED</text></g>'
            % (MARGIN_X, MARGIN_Y - 8, width - 2 * MARGIN_X, MARGIN_X + 12, MARGIN_Y + 9,
               esc(site_label.upper()), n_postings, "" if n_postings == 1 else "S"))


def void_panel(site, cov, width):
    """A site with nothing inferred gets a hook, not an empty frame."""
    state = cov.get("coverage_state", "never_scanned")
    lines = []
    if state == "never_scanned":
        lines.append("0 postings indexed")
    elif state == "scanned_none_found":
        lines.append("swept %s — 0 requisitions matched this site" % (cov.get("scanned_at") or ""))
    else:
        lines.append("%s requisitions indexed, %s admitted, %s bodies fetched, 0 org structure extracted"
                     % (cov.get("n_indexed", "0"), cov.get("n_admitted", "0"), cov.get("n_bodies", "0")))
    if site.get("named_site_head"):
        lines.append("named site head: %s" % site["named_site_head"])
    lines.append("ICP fit %s · %s" % (site.get("icp_fit", "?"), site.get("city", "")))
    g = ['<g class="void"><rect class="void-fr" x="%d" y="%d" width="%d" height="%d" rx="10"/>'
         % (MARGIN_X, MARGIN_Y + 26, width - 2 * MARGIN_X, 34 + 20 * len(lines))]
    g.append('<text class="void-h" x="%d" y="%d">NOTHING INFERRED FOR THIS SITE</text>'
             % (MARGIN_X + 18, MARGIN_Y + 52))
    y = MARGIN_Y + 74
    for ln in lines:
        g.append('<text class="void-b" x="%d" y="%d">· %s</text>' % (MARGIN_X + 18, y, esc(ln)))
        y += 20
    g.append("</g>")
    return "".join(g), 26 + 34 + 20 * len(lines) + MARGIN_Y


def render_tree(site, cov, units, edges, sup_labels):
    """-> svg string for one site lane."""
    roots, orphans, nodes = build_forest(units, edges)
    width = MARGIN_X * 2 + NODE_W + 3 * COL_W

    if not units:
        panel, h = void_panel(site, cov, width)
        return ('<svg class="tree tree-%s" viewBox="0 0 %d %d" style="width:%dpx">'
                '%s%s</svg>' % (site["site_id"].replace("--", "-"), width, h, width,
                                site_root_bar(site.get("city") or site.get("site", ""),
                                              int(cov.get("n_indexed") or 0), width), panel))

    TOP = MARGIN_Y + 40          # clears the site-root label bar
    cur, lvl = [TOP], {}
    for r in roots:
        tidy(r, 0, cur, lvl, sup_labels)
    unsqueeze(roots)
    # orphan band -- always below whatever the trees occupied, and below the site bar when there
    # are no trees at all (otherwise the band label lands on top of the site label)
    band_y = (cur[0] + BAND_GAP) if roots else (TOP + 18)
    for o in orphans:
        o.x = X(0)
        o.measure(sup_labels.get(o.uid, ""))
        o.y = band_y
        band_y += o.h + PAD_Y

    maxd = 0
    parts = []
    e_by_src = defaultdict(list)
    for e in edges:
        e_by_src[e["src_unit_id"]].append(e)

    def walk(n, d):
        nonlocal maxd
        maxd = max(maxd, d)
        yield n, d
        for c in sorted(n.children, key=lambda c: (-c.n, -CONF_RANK.get(c.conf, 0), c.name, c.uid)):
            for x in walk(c, d + 1):
                yield x

    placed = []
    for r in roots:
        for n, d in walk(r, 0):
            placed.append(n)
    placed += orphans

    # edges first so cards sit on top
    pos = {n.uid: n for n in placed}
    for e in edges:
        if e.get("status") == "rejected":
            continue
        s = pos.get(e["src_unit_id"])
        if not s:
            continue
        if e["edge_type"] == "reports_to":
            d = pos.get(e["dst_unit_id"])
            if d:
                parts.append(edge_svg(e, d, s, elbow(d.x, d.y, d.h, s.x, s.y + s.h / 2.0),
                                      "e-parent"))
        elif e["edge_type"] == "supports":
            d = pos.get(e["dst_unit_id"])
            if d and d is not s:
                sx, sy = s.x + NODE_W, s.y + s.h * 0.72
                tx, ty = d.x + NODE_W, d.y + d.h * 0.28
                parts.append(edge_svg(e, s, d,
                                      "M%.1f,%.1f C%.1f,%.1f %.1f,%.1f %.1f,%.1f"
                                      % (sx, sy, sx + 70, sy, tx + 70, ty, tx, ty), "e-supports"))

    for n in placed:
        extra = "n-orphan" if n in orphans else ""
        if n in orphans:
            parts.append('<path class="stub-dead" d="M%.1f,%.1f H%d"/>'
                         % (X(0) - 14, n.y + n.h / 2.0, X(0)))
            parts.append('<circle class="stub-cap" cx="%.1f" cy="%.1f" r="2.5"/>'
                         % (X(0) - 16, n.y + n.h / 2.0))
        parts.append(node_svg(n, extra))

    W = MARGIN_X * 2 + NODE_W + maxd * COL_W
    if orphans:
        by = (cur[0] + BAND_GAP - 8) if roots else (TOP + 10)
        parts.insert(0, '<g class="band"><line class="band-rule" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>'
                        '<text class="band-lbl" x="%d" y="%.1f">NO REPORTING LINE FOUND IN ANY '
                        'POSTING · %d TEAM%s</text></g>'
                     % (MARGIN_X, by, W - MARGIN_X, by, MARGIN_X, by - 6, len(orphans),
                        "" if len(orphans) == 1 else "S"))
    H = max(band_y + MARGIN_Y, cur[0] + MARGIN_Y)
    head = site_root_bar(site.get("city") or site.get("site", ""),
                         int(cov.get("n_indexed") or 0), W)
    return ('<svg class="tree tree-%s" viewBox="0 0 %d %.0f" style="width:%dpx">'
            '%s%s</svg>' % (site["site_id"].replace("--", "-"), W, H, W, head, "".join(parts)))

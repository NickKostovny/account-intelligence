# Account Intelligence — Handoff

**Read this first.** Verified state as of **2026-09-10**. Everything below was run, not planned.

---

## 0. 2026-09-10 session — the sales-facing pass (read before the older sections)

**PAUSED 2026-09-10 ~12:35 at Nick's request (usage limit). Resume here:**
- Live: `https://account-intel-invert.vercel.app` (SSO) and Nick's `_vercel_share` link (secret via `vercel api /v4/aliases/…`).
- Still running locally, no tokens: `data/cache/sweep_chain_20260910.sh` (probe was at 31/47 → Workday ingest → `jobs_cdx.py
  --all` → rollup → `build_org.py`); log `data/cache/sweep_run_20260910.log`, ends with `SWEEP DONE`. After it: `python3
  build-briefs.py && python3 stage_deploy.py && cd deploy && vercel deploy --yes --scope invert` → `vercel alias set <hash>
  account-intel-invert.vercel.app --scope invert` → `python3 stage_deploy.py --check-all`.
- NOT done: Clay ROLE searches to fill the PENDING-CLAY site heads for **Eli Lilly (11 sites) and Novo Nordisk (15 sites)**.
  Both agents were stopped before writing any `data/cache/clay/roles/*.json`. Recipe is ready: `merge_roles.py` consumes
  those files; the Clay DSL only accepts `headline` (see memory `reference-clay-mcp-limits`); the agent prompts (site lists,
  location strings, ranking, Email step, JSON schema) are in the chat of 2026-09-10 — relaunch one agent per account, then
  `python3 merge_roles.py && python3 build-briefs.py` and redeploy as above. Known Lilly hits from the account-wide query
  (task mcp-task_0tl5vaw92AEQgt4NBx2): Diane Tennenhouse (Site Head Houston, 504173641), Graciela M. Romero (VP Site Head
  Huntsville, 630716459), Stephanie Cass (AVP Manufacturing Site Head, Spain → Alcobendas, 440614317), Mo Behbahani (SVP Site
  Head/GM Drug Substance API, Indianapolis, 24851725), Jennifer Massey (AVP Manufacturing Site Head, Brownsburg IN, 755488194).

Nick's goal this session: show the head of sales the current state of the system. The build had
optimised for evidence integrity and org-map depth (2 of 49 accounts), so a rep opening any other
brief met a wall of zeros first. Four moves, in dependency order:

**A. Briefs lead with the complete layers.** `build-briefs.py` section order is now: stat chips
(pipeline status, deal owner, sites · KEEP, named heads, LinkedIn-verified, publications; reqs/units
only when present) → **Sites, KEEP first**, with a Verdict badge (hover = `icp_fit_reason`) and a
**Named site head** column → Publications → the whole posting layer (coverage bar, gap chips, pins,
org map, corpus query, digital/IT ledger) → lineage → hiring fold. The posting layer is wrapped in
`<details class="fold">` **unless the account has ≥1 observed org unit**; the summary says
"not swept yet" or "N requisitions indexed · no teams reconstructed yet". The DOM-ORDER CONTRACT
(§6) still holds because the radios, gap inputs and `#f-supports` move *inside the same* `<details>`
as `.map`. Verified in the browser: fold opens, 1 tree shown, toggles work. Placeholders in
`named_site_head` (`PENDING-CLAY` ×156, `n/a`, "Not identified", "None surfaced", site-closed notes)
render as empty via `head_text()`; a literal `None` in `deal_owner` is a null (`owner_of()`).
**Real named heads: 301 / 604 sites**, not the 531 a raw non-empty count suggests.
`index.html` groups accounts **Live pipeline → No deal yet → Closed lost**, sorted by KEEP-site count,
each card carrying status, owner, KEEP n/total, named heads, LinkedIn count, pubs (+ reqs/units when
present). Prose guard still passes. **Nick's rule (v3, same day): no explanatory text on the page** — legends, footers,
coverage notes, map hints, pin/filter help and source labels are gone; what remains is the header with `generated
<date>`, stat chips, tables, noun section titles and the numeric summary line. Method notes live here, not on the page.

**B. Clay-verified site heads = a new table.** `clay_targets.py` pulls proper names out of the
`named_site_head` prose on KEEP sites of active accounts (189 people / 39 accounts; a stoplist rejects
org words and the title must contain a title keyword), ranks by pipeline status (P1 live, P2 no deal,
P3 closed lost) and writes `data/clay_targets.csv` + 20-name batch files under
`data/cache/clay/batches/`. Subagents run `search-contacts-by-name` per batch (Clay MCP, workspace
**1243060**, one call per batch, results → `data/cache/clay/batchNN.json`), then `add-contact-data-points`
Email on found entities. `merge_clay.py` folds the batch files into **`data/site_heads.csv`**
(upsert by `site_head_id` = target_id; not-found rows kept with `found=false`; HTML entities
unescaped). `build-briefs.py` joins it by `site_ids` and renders the person as a LinkedIn link with
Clay's current title and "matched on LinkedIn <date>"; the site-map prose stays as the fallback,
labelled "site-map evidence". Company domains live in **`aiq.DOMAIN`** (curated; guessing
`bristolmyerssquibb.com` finds nothing). Email hygiene in `merge_clay.py`: an initials-only local part
(`m.c@gsk.com`, `ir@pfizer.com`) is `email_status=suspect`; an address whose domain is neither `aiq.DOMAIN` nor
one of the live subsidiary/legacy domains in **`aiq.EMAIL_DOMAINS`** (bmrn.com, janssen.com, seqirus.com…) is
`suspect_domain` (shire.com at Takeda, trilliumtherapeutics.com at Pfizer). Both stay in the table; the brief only
renders `found`.

**C. Sweep widened.** `ats_probe.py` re-run for all 47 unprobed accounts (Wayback CDX; slow, ~1 min
per account, cached under `data/cache/ats/`). `careers.csv` gained two columns, **`workday_host` and
`workday_site`**, so an account can carry an archive-crawlable careers host for `jobs_cdx.py` AND a
Workday tenant for `jobs_workday.py` without one collector overwriting the other; `jobs_workday.py`
now upserts the careers row instead of replacing it, discovers the tenant with the curated domain,
and `--all` selects on either column. Sequence run by `data/cache/sweep_chain_20260910.sh` (probe →
Workday ingest for the 10 `api_current_only`/workday accounts → `jobs_cdx.py --all` → rollup →
`build_org.py`); log in `data/cache/sweep_run_20260910.log`. **No collector exists for
SuccessFactors (astellas, bayer, biontech, boehringer, novo-nordisk) or Eightfold (alnylam,
astrazeneca's newer board)** — that is the next collector to write. Tier-2 extraction was NOT re-run;
org units still exist only for AstraZeneca and Biogen.

**D. Gated deploy moved to the `invert` team.** The old `invert-account-intel` project sits in the
personal `nick-8710s-projects` scope where the harness can neither read nor set protection (MCP 403).
New project **`invert/account-intel`** (`prj_qSXVDLc9RvRp1oWtM5r8y5KuyU8C`, team
`team_Xku7jES7Ne4OzSr6WyhwGHcC`) was created **empty**, then a canary page proved the gate:
`https://account-intel-<hash>-invert.vercel.app` and `https://account-intel-invert.vercel.app` both
**302 → vercel.com/sso-api**; the bare `account-intel.vercel.app` belongs to an unrelated third party
("Expert Financial Solutions"), so it can never be ours and must never be shared. `stage_deploy.py`
copies `briefs/` → `deploy/`, writes `vercel.json` (cleanUrls false, noindex + no-store headers) and
re-points `deploy/.vercel` (the old link is kept as `project.json.prev`); `--check <url>` fails unless
the unauthenticated fetch lands on the SSO redirect with none of the confidential markers in the FULL
body. Viewers need to be members of the Invert Vercel team (SAML) — or Nick presents it himself.
**Deployed 2026-09-10 (v2, production alias): `https://account-intel-invert.vercel.app`** — sales-facing layout +
Clay-verified heads. Final Clay numbers: **189 people checked → 147 on LinkedIn (78%) → 127 emails returned, 120
usable** (3 initials-only + 4 acquired-domain addresses held back). By pipeline priority: P1 live deals 66 checked /
50 LinkedIn / 44 emails · P2 no-deal 74 / 63 / 46 · P3 closed-lost 49 / 34 / 30. Zero Clay credit warnings. Gate re-verified after the
deploy: `-invert` alias and hash URL both land on the Vercel login, zero confidential markers in the full body.
`stage_deploy.py --check` strips the login page's `next=` URL echo before scanning, otherwise a marker that is also
a path slug (astrazeneca.html) false-positives. **INCIDENT, same day:** Vercel had assigned the project a default production alias **`account-intel-two.vercel.app`**
(because `account-intel.vercel.app` was taken). That default alias sits OUTSIDE Vercel Authentication on this plan and
served the briefs at **HTTP 200 unauthenticated for ~40 minutes** (the `--prod` deploys at ~12:05–12:45) on a hostname
nobody had been given. Caught by reading `targets.production.alias` via `vercel api /v9/projects/<id>`; removed with
`vercel alias rm account-intel-two.vercel.app --yes --scope invert` → 404. Lesson added to `stage_deploy.py`: **never
`--prod`**; deploy a PREVIEW and `vercel alias set <hash-url> account-intel-invert.vercel.app --scope invert`, then
`python3 stage_deploy.py --check-all` (asserts -invert = SSO, -two = 404). Redeploy recipe: `python3 build-briefs.py &&
python3 stage_deploy.py && cd deploy && /opt/homebrew/bin/vercel deploy --yes --scope invert` (sandbox off) → alias set →
`--check-all`. **Shareable link:** the MCP `get_access_to_vercel_url` cannot mint one for this project and the harness's
permission layer blocks `vercel api -X PATCH /aliases/<alias>/protection-bypass` (a protection-setting change), so a
`_vercel_share` link has to be created by Nick himself, or the viewer is added to the Invert Vercel team.
**A shareable link EXISTS (created by Nick 2026-09-10, no expiry):** `https://account-intel-invert.vercel.app/?_vercel_share=<secret>`.
The secret is deliberately not written here — read it with `vercel api "/v4/aliases/account-intel-invert.vercel.app?teamId=team_Xku7jES7Ne4OzSr6WyhwGHcC"`
(`protectionBypass`, scope `shareable-link`). Create: `echo '{}' | vercel api -X PATCH "/aliases/account-intel-invert.vercel.app/protection-bypass?teamId=…" --input -`
(body `{}` or `{"ttl": seconds}`; `generate` is rejected on the alias endpoint). Revoke: same call with
`{"revoke":{"secret":"<secret>","regenerate":false}}`. Verified: the link opens with no login and its cookie carries
into every brief; without the token the same URL still 302s to SSO. Anyone holding the link can read the briefs.

Previous handoff (2026-07-23) covered the 4-table SSOT + prose briefs. This session rebuilt the
job-postings layer around a different idea and rewrote the brief renderer. The older sections are
kept where still true.

---

## 1. The reframe that drove this session

Job postings were modelled as **buying signals** (20 rows in `signals.csv`). The Jul 23 call
corrected that: they are a **context corpus** — the richest available source for reconstructing an
org's internal structure, which is invisible on LinkedIn because titles are self-authored and
flattened while postings are org-authored and have to describe where a role sits in order to hire
for it.

Concrete proof the old shape under-captured: `sig00044` **was** the "Director, Process Data Science
and Statistics" req walked through live on that call. In our table its team and parent org were
mashed into a prose `topic` field, `tools_named` was **empty** (the real posting names Snowflake and
Streamlit), and its `url` was a Glassdoor re-host — not even re-fetchable to verify.

`job_posting` no longer exists as a `signal_type`. Postings live in their own tables.

---

## 2. Architecture — two tiers, two collectors

**Tier 1 — deterministic, no LLM.** Requisition index: req id, city, role title, capture window →
job family, seniority, site. Zero fabrication risk because nothing is inferred by a model.

**Tier 2 — LLM, filtered subset.** Body text → team name + acronym, parent org, teams supported,
tech stack, vocabulary, hiring manager, each with the verbatim line it came from.

**The single most important design decision:** *Python owns the bodies.* `fetch_bodies.py` fetches
and caches body text; the workflow **embeds that text in the prompt** and the agents never touch the
network. Consequences: quote verification is an exact substring test against our own copy, no tokens
are burned on dead URLs, and runs are reproducible. Measured result — **241/241 quotes verified, all
exact matches, 0 claims dropped.**

### Two Tier-1 collectors, because there are two kinds of careers site

| Collector | Works on | Gives | Verified |
|---|---|---|---|
| `jobs_cdx.py` (Wayback CDX) | Phenom-style server-rendered careers sites | ~2yr **history** | AstraZeneca 6,677 req URLs → 5,097 unique |
| `jobs_workday.py` (Workday CXS JSON API) | Workday tenants | **current** snapshot + real posted dates | Biogen 203 reqs, 110 with posted dates |

**Workday has NO Wayback history at all** — requisition pages are JS-rendered and were never
archived. Biogen returned zero across 8 guessed hostnames and 4 URL globs. The CXS JSON API is the
only route, and it is a good one: 100% body yield at `tier3_live_body` vs ~50% from the archive.

**Workday tenant names cannot be guessed.** Biogen's is `biibhr`, not `biogen`.
`jobs_workday.py --discover <aid>` reads the real ATS host out of the company careers page.

**The refresh IS the time-series generator.** `first_seen`=min, `last_seen`=max, `seen_count`+=1 per
run. Wayback backfills a skeleton of the past; uniform sampling from here is the only unbiased
series we will ever have. A run-from-scratch design would destroy the exact signal §4.4 of the brief
asked for.

---

## 3. Files

```
aiq.py            shared: city->site resolution, families, ids, WRITE LOCK, overrides
ats_probe.py      careers-host + req-URL-pattern discovery -> data/careers.csv
jobs_cdx.py       Tier-1 collector A (Wayback) + --rollup share-of-mix + --resolve-only
jobs_workday.py   Tier-1 collector B (Workday CXS JSON API) + --discover
fetch_bodies.py   body text -> data/cache/bodies/<posting_id>.txt   (Python owns the bytes)
gen_org_wf.py     emits wf/*.wf.js with bodies EMBEDDED as const DATA
verify_org.py     THE VERBATIM GATE. exact-substring check, quarantines failures, exits 1 <95%
merge_org.py      -> posting_extracts.csv (tier-monotonic upsert) + posting_quotes.csv (append-only)
build_org.py      -> org_units.csv, org_edges.csv, org_site_coverage.csv
orgmap.py         layout math + SVG for the spatial org map
build-briefs.py   49 briefs + index + jobs-<aid>.json, with the PROSE GUARD
mcp/org_intel_server.py   MCP: structured records only, 9 tools
org_overrides.json        HUMAN judgement. never regenerated.
wf/, data/cache/          generated workflows, cdx + body caches
../org_out_20260729.json  AZ extraction result (durable)
../org_stage0.json        Stage-0 gate result (durable)
```

**Tables** (`data/`): `accounts, sites, people, signals` (unchanged) + `postings, posting_extracts,
posting_quotes, org_units, org_edges, org_site_coverage, posting_rollup, careers, quarantine_org,
jobs_runs`.

IDs in the new tables are **content-derived, not counters** (`{aid}--jr-{req_id}`,
`{aid}--ou-{slug}`). A counter renumbers when the CDX result set legitimately changes and every human
annotation reattaches to the wrong row.

---

## 4. Verified numbers

| | |
|---|---|
| postings | **5,301** (AstraZeneca 5,097 · Biogen 204) |
| AZ site resolution | 1,037 exact + 102 alias + 2 metro · **420 ambiguous** (correctly unresolved) · 3,517 city-not-in-sites |
| AZ tier-2 admitted | 160 · bodies fetched 74 usable / 26 unavailable (~50% archive yield) |
| Biogen | 68 site-resolved, 15 admitted, **15/15 bodies (100%, tier3_live)** |
| quote verification | **403/403 exact (100%)**, 0 quarantined, 7 unsupported claims dropped |
| org units | **21 observed** + 68 named-but-unmapped ghosts |
| org edges | **98** — 14 reports_to, 63 supports, 21 located_at (10 medium confidence, 88 low) |
| rollup | 89 rows, 23 carry a bias flag, **0 flagged rows missing a bias_note** |
| briefs | 49 + index, **0 `<p>` tags**, 170 blockquotes all inside `.ev-src` |
| FK violations | **0** |

Real recovered structure (AZ): Integrated Bioanalysis **(IBA)** ← Clinical Pharmacology and Safety
Sciences · Bioinformatics ← Oncology Data Science · Statistical Programming ← Vaccines and Immune
Therapies · Oncology Strategy, Business Development & Alliances **(SBDA)** ← Oncology R&D · Machine
Learning and AI Operations **(ML/AI Ops)** ← Evinova · Commercial Reporting & Analytics **(CRA)** ←
GBS Commercial Operations.

---

## 5. Bugs found and fixed — do not reintroduce

1. **`ncity()` stripped non-ASCII.** `Södertälje` → `sdertlje`, URL slug is `sodertalje`. 202 AZ reqs
   orphaned. Fixed by `aiq.afold()`.
2. **`ncity()` stripped parentheticals** — but `Mölndal (Gothenburg)` carries the name the careers
   site uses. Parentheticals are now indexed as aliases.
3. **City collisions resolved to the wrong site, silently.** `site_for()` used `setdefault`, so
   first-wins. AZ has `cambridge` twice (Kendall Sq MA / Cambridge UK) and `sodertalje` twice; **77**
   city-level collisions exist across active accounts. `resolve_site()` now returns
   `ambiguous` + the candidate list and **never guesses**. Settle one permanently in
   `org_overrides.json` → `site_picks`, then `jobs_cdx.py --resolve-only`.
4. **Comma-splitting indexed US state codes as cities.** `"Cambridge, MA"` produced the key `ma`,
   which matched sites across 30 accounts. `city_keys()` keeps the city proper and drops short geo
   tokens.
5. **Workday city slugs carry a state suffix.** `Cambridge-MA` → `cambridgema` missed a site index
   built from `Cambridge, MA`. `slug_to_key()` strips trailing short tokens.
6. **Subsidiaries double-counted.** New Haven CT is both `astrazeneca--s11` and `alexion--s01`, and
   `careers.astrazeneca.com` serves Alexion reqs. `mirror_for()` records
   `mirror_account_id` + a `mirror_basis` of `dual_registered` or `subsidiary_only` — the two carry
   different certainty and neither is asserted silently.
7. **`site_id` is NOT a durable key.** `normalize.py` reassigns it from a per-account counter driven
   by source-CSV row order. Accreting tables persist `city_slug` and re-resolve every run
   (`_rescore()` / `--resolve-only`).
8. **CDX timestamps are first-capture, not posted, dates.** AZ 2024Q2 showed 304 data/analytics reqs
   vs 12–41 elsewhere — a crawl burst, not a hiring burst. **Never chart raw `n`.** Use
   `share` → `self_index` → **`peer_index`** (share ÷ median share across accounts in the same
   quarter; the crawl regime divides out). `bias_flag` + a precomputed `bias_note` travel with every
   rollup row.
9. **Interns slipped through Tier-2 admission** via an ORG_CORE family. `HARD_EXCLUDE` (intern,
   apprentice, grad programme) now always wins; `SOFT_EXCLUDE` (commercial, sales) can be overridden
   by an ORG_CORE family.
10. **CONCURRENT WRITES DESTROY ROWS.** Running `fetch_bodies.py` in the background while
    `jobs_workday.py` ingested Biogen **wiped all 203 Biogen rows** — the body-fetcher still held a
    pre-Biogen snapshot and checkpointed it back. Every writer now holds `aiq.lock()` across the
    whole read-modify-write, and `fetch_bodies.py` uses `aiq.patch()` (re-read under the lock) instead
    of writing a snapshot. **Do not add a writer that does load→modify→write without the lock.**
11. **HTML entities double-escaped.** `Oncology R&amp;D` rendered as `R&amp;amp;D`. `merge_org.py`
    unescapes once on ingest.
11b. **The concurrency bug bit a SECOND time** after the lock was added, because the long-running
    `fetch_bodies` process still had the pre-patch code *and* its stale row set in memory — Python
    does not reload a running module. Killing the process fixed it. `aiq.write()` now **refuses to
    shrink** an accreting table by more than 2% (`AIQ_ALLOW_SHRINK=1` to override), so the symptom is
    loud instead of silent. Before any pipeline run: `ps aux | grep -E "fetch_bodies|jobs_"`.
11c. **Claim-to-quote matching was too strict.** An extractor legitimately groups several teams under
    one line ("...to FP&A and preferably other Corporate Functions, such as Procurement, HR, Legal,
    Compliance" backs four supports_teams entries). Requiring an exact subject match dropped 96
    Biogen claims that had all passed the verbatim gate. `backed()` now falls back to containment —
    the quote must still be verified AND actually name the subject, so the guarantee is unchanged;
    drops fell to 7.
12. **TWO FEATURES SHIPPED INERT and were demoed as working.** Both were pure-CSS toggles whose rules
    could never match:
    - **Zoom** — `--z:1` was declared on `:root` and both SVG emitters used
      `width:calc(var(--z) * Npx)`, but the `#z-*:checked ~ .map{--z:…}` rules were never written.
      Removed entirely rather than fixed; trees emit at 276-812px and the canvas scrolls.
    - **Gap chips** — the `#g-*` inputs were emitted *inside* `<div class="gchips">`, so
      `#g-orphan:checked ~ .map` had no matching sibling. `.fin:checked + .gchip` DID match, so the
      chip turned solid and looked active while the map never dimmed. Fixed by hoisting the inputs to
      `.wrap` level ahead of `.map`; the labels stay in the chip row.
    Lesson: a `:checked ~ selector` toggle needs a browser assertion that the *target* changed, not
    just that the control restyled. Both are now in the verification list.
13. **A place-name mismatch silently zeroed a site.** Biogen's spine says `Durham, NC`; Workday returns
    `Research Triangle Park, NC` for 39 reqs, so they resolved to nothing and the Durham lane
    truthfully reported the zero it was given. Nick caught it by intuition. Fixed with a curated
    `PLACE_ALIASES` map in `aiq.py` (reverse-indexed into the ALIAS bucket, so it can never outrank a
    real city match and a synonym covering two sites still reports AMBIGUOUS). The honest other half is
    `data/unmatched_cities.csv`, written by both collectors and ranked by req count, which separates
    `needs_alias` from `missing_site` from `no_place`. It immediately surfaced Biogen's
    `Baar, Switzerland` (17 reqs) — their Swiss commercial HQ, genuinely absent from the 604-site
    spine, which is a finding for the site-mapping work rather than a bug here.
14. **A reporting-line CYCLE deleted teams from the map.** Two Biogen postings each named the other
    team as parent, so both had a parent, neither was a root, neither was an orphan, and both vanished:
    the header said 7 teams and 5 rendered. `build_forest()` now detects cycles, breaks the
    weakest-evidenced link (fewest postings, then lowest confidence, then id for determinism) and
    **marks the node** `⟳ POSTINGS DISAGREE ON REPORTING LINE` — a conflicting reporting line is a real
    finding about the sources, not something to resolve quietly.
15. Earlier gotchas that still hold: Workflow `args` arrives undefined for sizeable payloads (embed
    as `const DATA`); signal dedup key must be title+url; source CSVs have a banner row + appendix;
    `python3` is 3.9 (no `match`, no `|` unions); session scratchpad is wiped between sessions.

---

## 6. The brief — org map as primary view

Three-column grid: **spine** (site lanes, ranked by req volume, each a bar you read by length *and*
a row you scan like a bullet — that is the resolution of "spatial" vs "scannable": one DOM, two
reading modes) · **canvas** (org tree for the selected site) · **dock** (evidence).

- Layout is computed in Python (layered Reingold–Tilford) and baked into SVG. Stable, diffable,
  self-contained, no CDN, no layout library. Works with JS off.
- **No edge without a verified quote.** `build_org.py` raises rather than write one.
- A parent named but not reconstructed becomes a **ghost node** that occupies layout space and
  pushes real siblings down — dashed, italic, "NAMED, NOT MAPPED", with a `?`.
- Unparented units go in a labelled band with a dead stub running off into an open circle.
- **Never-scanned ≠ nothing-found.** Never scanned gets a hatched full-length bar (an absent bar
  would read "small", which is a lie); scanned-and-empty gets a 3px tick. `org_site_coverage.csv`
  carries the four states.
- Unknown tech renders a dashed `stack ?` chip — omitting the row would shrink the card and hide the
  gap.
- Provenance is `:target`-driven: zero JS, back-button correct, **deep-linkable**
  (`astrazeneca.html#ev-<unit_id>` lands a colleague on the exact node).
- Judgment overlay in `localStorage` (`aiq:v1:<account>`): pin / confirm / reject / note, with
  content-addressed refs so it survives a rebuild, an export→`org_overrides.json`→commit path, and a
  reconciliation prompt (default **keep**) for refs that no longer resolve.
- **PROSE GUARD:** the build asserts 0 `<p>` tags and that every `<blockquote>` sits inside
  `.ev-src`. Prose is admissible as quoted evidence, never as conclusion. `hyp_block()` is gone,
  replaced by evidence ledgers, a lineage timeline and derived meters. `--allow-prose` bypasses.

**Section order** (changed 2026-07-30 after Nick reviewed it cold). Publications moved up because he
called it out as the part he uses; the hiring table moved into a collapsed fold at the bottom because
it was 79-83% "unclassified" and read as noise:

1 header + stat chips · 2 coverage bar + gap chips + unmatched-location line · 3 **My pins** ·
4 **Org map** (legend + supports toggle *below* it) · 5 Sites · 6 **Publications, talks and news** ·
7 Query the indexed corpus · 8 Where digital/data/IT sits · 9 Acquired-company lineage ·
10 collapsed fold: hiring concentration + the export/import panel.

**DOM-ORDER CONTRACT — do not break it.** `%(radios)s`, `%(gapinputs)s` and `#f-supports` must stay
siblings of `.map` at `.wrap` level AND precede it; the whole toggle layer is `#id:checked ~ .map`
CSS with no JS. `.ev-empty` must stay the last child of `.dock`. `%(js)s` must stay after all markup.

**Label vocabulary** (jargon was the main comprehension problem):
`UNPARENTED …` → **`NO REPORTING LINE FOUND IN ANY POSTING · N TEAMS`** ·
`NAMED, NOT MAPPED` → **`INFERRED — NAMED IN A POSTING ONLY`** ·
a bare `medium` pill → **`MEDIUM CONFIDENCE · 2 postings, 2 verified quotes`** (one line, the separate
"basis" chip is gone) · gap tokens now render through `GAP_LABEL` in plain English ·
`ORG UNIT` → `TEAM`, edge types through `EDGE_LABEL`. A legend under the map explains every stroke.

Preview: `.claude/launch.json` → `account-intel-map` on **port 8831**.

---

## 7. Rebuild recipe

```bash
cd "/Users/nickkostovny/Website Marketing Landing Pages/account-intelligence"
python3 normalize.py                       # source CSVs -> the 4 original tables
python3 merge.py ../enrich_out.json        # publications/news/etc back in
python3 ats_probe.py                       # careers hosts (network; cached)
python3 jobs_cdx.py --all                  # Tier-1 A: Wayback  (network; cached)
python3 jobs_workday.py --all              # Tier-1 B: Workday tenants
python3 jobs_cdx.py --rollup               # share-of-mix + peer baselines
python3 fetch_bodies.py --account <aid>    # bodies (Python owns them)
python3 gen_org_wf.py --account <aid> --pending --shard 8 --max-agents 6 --out wf/org-<aid>.wf.js
#   Workflow({scriptPath: ".../wf/org-<aid>.wf.js"}) -> save .result to ../org_out_<date>.json
python3 verify_org.py ../org_out_<date>.json    # GATE. exits 1 below 95%
python3 merge_org.py ../org_out_<date>.json
python3 build_org.py
python3 build-briefs.py
```
Network steps need Bash with `dangerouslyDisableSandbox: true`. **Never run two writers at once.**

---

## 8. Open items

**Gated deploy — STAGED, NOT DEPLOYED. Read this before retrying.**
A first attempt deployed to a new project and the deployment returned **HTTP 200 with no SSO wall**
— i.e. the confidential briefs were on a publicly reachable URL. It was removed within ~4 minutes
(now HTTP 410) and the hostname was random and never shared. Cause: a brand-new Vercel project's
protection state must be verified *before* content goes up, not after.
Current state: project **`invert-account-intel`** exists
(`prj_p8Eaipc1gNPu0TUb93k96i7ssUeq`, team `team_1DQMolrWIeIPU5MOUpcNi01B`), is linked from
`deploy/`, and reports `ssoProtection: {deploymentType: all_except_custom_domains}`. Content is
staged in `deploy/` (50 html + 50 json + `vercel.json` with `cleanUrls:false`, because briefs
cross-link by `<account>.html`). Setting protection to `all` is **rejected on this plan** ("not
available for production deployments"), so deploy as a **preview** only.
To finish: `cd deploy && /opt/homebrew/bin/vercel deploy --yes --archive=tgz`, then **fetch the
resulting URL and confirm 401/SSO on the full response body before sharing it anywhere.** Read the
whole body, not the first 20KB — the first 20KB is CSS, which made a leak check pass that should
have failed.

**Coverage.** `ats_probe.py` had resolved 11–12 of 49 accounts when the session ended (Phenom-style
sites resolve well: AbbVie, Amgen, Alnylam, Alexion→AZ). It is safe to re-run; the per-probe cache
makes it cheap. 5 accounts were `needs_discovery`; several of those are likely Workday and should go
through `jobs_workday.py --discover`.

**Biogen is DONE** — 204 reqs, 15 extracted, 162/162 quotes verified, 7 org units
(Decision Sciences, US Decision Sciences, Biostatistics, Field Analytics and Reporting, Small
Molecule Chemical Development, Head-of-FP&A-IT under `IT department` reporting to the VP of Corporate
Functions IT). Honest caveat: because Workday gives no history, Biogen shows only what is **open
right now**, and its current openings skew commercial/clinical rather than bioprocess. The bioprocess
reqs it does have are scientist-level and fall below the Tier-2 seniority bar — lower it with
`fetch_bodies.py --min-score` if bioprocess org structure matters more than budget.

**Refresh task ARMED:** `ai-jobs-index-biweekly`, cron `0 6 1,15 * *`, Tier-1 only (pure Python, no
agents, ~0 tokens), stored at `~/.claude/scheduled-tasks/ai-jobs-index-biweekly/SKILL.md`. It runs
the collectors strictly in sequence and is explicitly told never to run two writers at once. The
monthly Tier-2 task is **not** armed yet — arm it prompt-gated for the first two cycles so a prompt
regression cannot quietly poison the corpus.

**Not yet done:** refresh scheduled tasks (`mcp__scheduled-tasks__*` — nothing in this repo has ever
used them, so **smoke-test one throwaway task first**; then biweekly Tier-1 pure-Python, and monthly
Tier-2 **prompt-gated for the first two cycles**). Clay contact waterfall on the 1,854 people.
Pruning the 53 low-confidence publications. HubSpot write-back stays gated.

**Honest limits.** Real posted dates for the bulk of the archive back-history are unrecoverable —
volume over time is share-of-mix, permanently. Full-text extraction over a whole archive is a
category error (~15M input tokens for AZ alone), which is what Tier 1 exists to avoid.
`hiring_manager` will stay near-empty: most postings name nobody. Whether a req was filled, or by
whom, is not in this data.

**Stage-0 note.** The PDSS req that motivated the build is **unrecoverable**: Glassdoor serves 403
behind a Cloudflare CAPTCHA, there are zero Wayback snapshots, and no fetchable re-host exists. The
extraction agent found org-structural claims about it in *search snippets* and correctly **refused**
to record them, because a snippet is not a page it read and cannot be quote-verified. That refusal
under pressure is the strongest evidence the anti-fabrication contract works.

---

## 9. MCP surface

`mcp/org_intel_server.py` — 9 tools, structured records only:
`list_accounts, list_org_units, get_org_unit, list_org_edges, get_provenance, query_postings,
get_hiring_mix, list_gaps, get_vocabulary`.

Built under a constraint from the call — the rep team rejected "ask a question, get a paragraph,
paste it into an email". So: **no tool accepts a natural-language question and no response schema
has a summary/answer/analysis field.** There is nowhere for a paragraph to live. Every record
carries provenance ids; quarantined quotes are never served. Verified by smoke test.

Register: `claude mcp add org-intel -- python3 "<abs>/mcp/org_intel_server.py"`

`/org-query` skill at `.claude/skills/org-query/SKILL.md` covers targeted top-ups, gap explanation
and host discovery.

> Note for whoever picks this up: the MCP surface was built at Nick's explicit direction. The
> internal customer who raised the original objection has **not** signed off on it. It is designed so
> the objection lands on the interface rather than the data.

---

## 10. Jev (TypeSafe) pilot — 2026-09-28

Jev = TypeSafe's text-only model that returns typed answers (Choice / Noul / Score) with
probabilities, never text. $0.042 per 1M input tokens; the state is billed ONCE per request, not
per question (measured: 1 question 1,532 tokens, 10 questions 1,712). Model pinned `jev-1.13.0`.

**Files (new, nothing in the SSOT touched):** `segment.py` (one-line bodies → numbered sentence
segments, each an exact slice of the body), `jev_client.py` (python 3.9 urllib client; key from
`$TYPESAFE_API_KEY` or Keychain item `TYPESAFE_API_KEY`, never on disk; response cache + usage log
in `data/cache/jev/`), `pilot_jev.py` (writes only `data/pilot/`).

**Scores on the 32 extracted postings (agreement with Claude, not accuracy):**
- Segmenter: 392/403 verified quotes fall inside one segment (the 11 others span two sentences).
- site_stated 31/32, posted_date 30/32 with **plain regex, no model** (the 2 date "misses" print two
  dates; Claude took the footer one).
- tech: 81/107 Claude tools at threshold 0.6; 9 Jev-only picks, mostly real assays Claude missed
  (ligand binding, cytokine, viral, polydispersity). Most misses are concept terms Claude filed as
  tools (zero trust, NIST, ISO, SaaS, martech, decision engines, gen AI). Real misses: Google ADK,
  IQVIA, Symphony. False positives at 0.5: LinkedIn, Instagram, QQ, "R", Data Lake.
- autonomy_flag: 27/32; 12/12 at confidence ≥ 0.9. Jev never picked the rare classes
  (site_autonomous 1, mixed 2 in the gold), so those stay with Claude or a person.
- Cost of the whole pilot ≈ $0.20, mostly the ungated tech pass (~$0.005 per posting). Claude
  Tier-2 measured ≈ $0.27 per posting.

**Not tested yet:** team_name, relations (reports_to / parent_org / supports), Clay site-head
picks, persona-SOP gate (sends people data to TypeSafe — needs Nick's OK first).

**Site ICP verdict pilot (same day, Nick approved sending site text — no names — to TypeSafe):**
`pilot_jev.py site` asks 10 atomic questions per site (status, drug substance, PD/MSAT, drug product,
manufacturing, support functions, research, modality, business, CDMO); `site_verdict()` combines them
in code and returns KEEP/FLAG/GAP/DROP/CLOSED/REVIEW plus the first failed condition as a tag.
Verdict words Claude left in `functions` are stripped first. Result on 604 sites ($0.03, 25 s):
decided 460 (76%), agree with Claude 348/460 (76%). 42 of the 112 disagreements are EU
small-molecule/mixed sites Claude kept but the US June re-pass would FLAG — a policy gap in the map,
not a Jev error; without them agreement is 83%. Other splits are also policy: R&D-only offices
(Claude DROP, rule FLAG), animal health / non-core (Claude mixed). Status must be judged by
probability MASS of closed/planned, not top-option confidence (operating vs not-stated both mean
carry on). Open: Nick's calls on those policies; a ~60-site human-labelled accuracy set before any
`icp_fit_reason` write to the SSOT.
**Nick's policy calls (2026-09-28):** EU gets the US June re-pass (small-molecule-only AND mixed →
FLAG, both regions); R&D-only offices → FLAG; CDMOs are customers → KEEP (tag `CDMO`, info only).
Animal health still undecided. Under this policy 50 map rows change, and Jev + rule agree on
398/460 decided sites (87%; EU 93%, US full text 80%, US short tag lists 78%, 7/7 manual rows where
decided). SSOT not written yet — needs the 60-site label set and Nick's go.

**Wishlist fill pass (2026-09-28, local build only — NOT deployed).** New scripts, outputs only in
`data/jev/` (SSOT checksums 19/19 unchanged, `data/jev/ssot_checksums_before.txt`):
`jev_site_profile.py` (20 Nouls/site → function + modality chips; 4% unsure; account modality
footprint rollup), `jev_site_fit.py` (policy verdict + reason tag + code explanation; 60-row blind
label sheet `data/jev/site_label_sample.csv` for Nick), `jev_postings.py` (12,634 unlabeled unique
titles → family / digital / bioprocess / seniority; family coverage 14.6% → 19.9%, 25% unsure;
url dedupe 32,038 → 26,018), `jev_digital_it.py` (where digital/IT reqs sit: 1 concentrated, 8
spread, 11 thin, 9 none, 45 no postings), `jev_publications.py` (relevance band; 403/512 kept).
Total Jev spend for the day ≈ $1.15. `build-briefs.py` renders all of it (backup
`data/cache/build-briefs.py.bak-20260928`); model name/p-values only in title attributes; hiring
cell only on sites that make/develop product (city-join false signals); thin digital evidence shows
no chip. Found: `aiq.FAMILIES` data_digital 'analytic' also matches Analytical titles (not fixed).
Still open: news, talks, legal entity/lineage, decision power, people/persona (needs approval).
**Label set moved to a shared Artifact** (https://claude.ai/artifact/7qZxiu9E78jqsC4kBjcpWv, private until Nick shares; db collections `sites`, `answers`). CGT rule: bare "CGT"/"cell and gene therapy" → both chips (33 sites), specific mentions keep Jev split.

**Automated source check MVP (2026-09-30).** `jev_verify_sources.py` + two adjudication workflows
replaced the human label pass for the 60-site sample: evidence agents saw only company/site/city,
code re-fetched pages and kept only verbatim quotes (54/60 sites), Jev re-asked SITE_QS from the
quotes claim by claim (a Noul "no" from a short quote is never a contradiction), Claude adjudicated
every disagreement and a second agent tried to refute each change. Result (data/jev/mvp_final.csv,
mvp_summary.json): notes (policy map) right 43/57, Jev alone right 34/43, 3 need a person
(Pfizer Sanford, Lonza Lexington, Sanofi Orlando). When notes and Jev agree: 21/21 right; when they
disagree one is right every time (Jev 13, notes 9); Jev unsure: notes right 13/14. So deploy the
policy map verdict where Jev agrees, and send only disagreements + REVIEW (~206 of 604) to the source
check. Leadership page: https://claude.ai/artifact/71GupGG6PRXHgW2wfDL1iL (private until shared).
Caution: the local briefs' Fit column still shows the raw Jev verdict; switch it to the checked /
agreed verdict before deploying.

**Full source check + news collector (2026-09-30, local build only, NOT deployed).**
- Source check on all disagreement/REVIEW sites (167 new + 39 from the trial): evidence workflow →
  `jev_verify_sources.py check206_evidence.json check206` → pipelined adjudicate+skeptic workflow →
  `jev_check_merge.py check206` → `jev_site_final.py` → `data/jev/site_verdict_final.csv`
  (377 readings agree · 201 checked · 26 need a person). On the 201 hard cases the policy map was
  right on 144; 57 changed, 14 of them only because of the open animal-health rule. 18 of 207 source
  links are Wikipedia (weak). Briefs' Fit column now reads site_verdict_final (verdict · reason ·
  source link; basis in the title attribute); index counts too.
- News: `news_collect.py` (Google News RSS, 277 queries, cached in data/cache/news/, `--refresh` to
  refetch; url = news.google.com redirect) → `jev_news.py` (about-company + ops + 7 trigger Nouls +
  site Choice; dedupe of one story across outlets) → data/news/{news_labels,site_news,account_news}.csv.
  6,708 headlines → 964 stories, 48 accounts, 91 sites; Jev $0.28. Briefs show up to 4 account
  stories and a per-site News column. Known weak spot: drug-label "expansion" read as capex.
- Jev total to date $1.43. Leadership page v2: https://claude.ai/artifact/71GupGG6PRXHgW2wfDL1iL
**Deployed 2026-09-30:** animal health → KEEP (Nick; `pilot_jev.site_verdict` no longer treats it as
non-core, `jev_site_fit.py` moves an animal-health map FLAG with it, `jev_site_final.py` turns a
checked FLAG whose only reason is animal health into KEEP: 13 sites). Verdicts: KEEP 223, FLAG 222,
DROP 83, GAP 29, CLOSED 21, REVIEW 26. Preview `https://account-intel-1nnh6jb82-invert.vercel.app`
gate-checked (PASS), then `vercel alias set … account-intel-invert.vercel.app`; `--check-all` PASS
(-two 404). Nick's existing share link serves the new build (verified: generated 2026-09-30, News
column, source links). Backups: data/cache/*.bak-20260930.

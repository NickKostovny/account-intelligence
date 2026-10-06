---
name: org-query
description: Run a targeted query or top-up against the account-intelligence job-postings org corpus — index one account, extract org structure from specific requisitions, or re-render a brief. Use when someone pastes a requisition URL from a brief's "copy /org-query" button, asks to refresh one account's postings, wants org structure pulled for a named site or job family, or asks why a team/site shows no data.
---

# org-query

Targeted operations on the job-postings org corpus in `account-intelligence/`. This is the honest
version of the "run an individual little query" affordance: the briefs are a **static, SSO-gated
deploy with no backend**, so the in-page filter queries a pre-built snapshot and anything that needs
a fetch or an LLM runs here, as a command.

Read `account-intelligence/HANDOFF.md` first if you have no context.

## Rules

- **Never** hand-edit `data/*.csv`. Every table is produced by a script.
- **Never** skip `verify_org.py`. It is the only thing standing between a fabricated team name and a
  sales email. If it fails the threshold, stop and report — do not merge.
- Human judgement lives in `org_overrides.json`. Read it, add to it, never regenerate it.
- Requisition capture dates are Wayback **first-capture** dates, not posted dates. Never present a
  raw per-quarter count as hiring volume; use `share` / `peer_index` from `posting_rollup.csv`.
- All scripts are python3 3.9, stdlib only. Network steps need Bash with
  `dangerouslyDisableSandbox: true`.

## Modes

### `/org-query req <url>` — one requisition
Someone clicked "copy /org-query" next to an un-extracted row in a brief.

1. Find its row: `python3 -c "import aiq;[print(r['posting_id'],r['extraction_tier']) for r in aiq.load('postings.csv') if r['url']=='<url>']"`
2. `python3 fetch_bodies.py --account <aid> --limit 1` won't target one URL — instead confirm the
   body is cached at `data/cache/bodies/<posting_id>.txt`. If absent, the body could not be
   recovered; report that and stop (a delisted req with no archive capture is a dead end, not a
   retry).
3. `python3 gen_org_wf.py --account <aid> --pending --shard 1 --max-agents 1 --out wf/org-one.wf.js`
4. `Workflow({scriptPath: "<abs>/wf/org-one.wf.js"})`
5. Save the result's `.result` to `../org_out_<YYYYMMDD>.json`, then
   `python3 verify_org.py ../org_out_<YYYYMMDD>.json` → `python3 merge_org.py …` →
   `python3 build_org.py` → `python3 build-briefs.py --account <aid>`
6. Report: what team/parent/stack was recovered, the verify pass rate, and what stayed empty.

### `/org-query account <aid>` — top up one account
1. `python3 jobs_cdx.py --account <aid>` (re-sweep; accretes, never loses history)
2. `python3 fetch_bodies.py --account <aid> --limit 48`
3. `python3 gen_org_wf.py --account <aid> --pending --shard 8 --max-agents 6 --out wf/org-<aid>.wf.js`
4. Workflow → verify → merge → `build_org.py` → `build-briefs.py`
5. Report new units, new edges, and the body-fetch yield (typically ~50–60%: many reqs are delisted
   with no usable archive capture).

### `/org-query site <aid> <site_id>` / `--family <family>` — narrow a top-up
Same as `account`, but pre-filter which requisitions get a body fetch:
`python3 fetch_bodies.py --account <aid> --min-score 20 --limit 24`, then generate the workflow with
`--min-score`. Use when a rep asks "what's the org at Macclesfield" and Macclesfield is
`indexed_not_extracted`.

### `/org-query refresh <aid>` — Tier 1 only, no LLM
`python3 jobs_cdx.py --account <aid> && python3 jobs_cdx.py --rollup && python3 build-briefs.py --account <aid>`
Cheap, ~30s, no tokens. Updates the index, the site-power ranking and the hiring mix.

### `/org-query why <aid> <site_id>` — explain a gap
No fetching. Read `org_site_coverage.csv` for that site and report the actual reason:
- `never_scanned` — this account has no corpus yet; run `/org-query account <aid>`
- `scanned_none_found` — swept, no requisition resolved to this city
- `indexed_not_extracted` — reqs indexed but no body fetched or none admitted; check `n_admitted`
  vs `n_bodies` and say which
- `covered` — units exist; link the brief anchor

Also check `postings.csv` for `site_resolution=ambiguous` on that account: a city shared by two of
its sites (AstraZeneca has `cambridge` and `sodertalje`) is deliberately left unresolved. The fix is
a human decision recorded in `org_overrides.json` under `site_picks`, e.g.
`"astrazeneca/cambridge": "astrazeneca--s18"`, then `python3 jobs_cdx.py --resolve-only`.

### `/org-query discover <aid>` — the careers host is unknown
`ats_probe.py` wrote `probe_status=needs_discovery`. Find the real host: search for the company's
careers site, confirm the requisition URL shape, add it to the `SEED` dict in `ats_probe.py`, then
`python3 ats_probe.py --account <aid>`. Report the host, the URL pattern and whether city is
parseable from the path — if it is not, that account gets titles and dates but no site attribution,
and the brief will show its sites as `scanned_none_found`.

## What this skill does NOT do

- It does not write copy, summarise an account, or draft outreach. It returns records and rebuilds
  pages; the narrative is the rep's job. If asked for a summary, surface the records and the
  provenance instead.
- It does not write to HubSpot.
- It does not deploy. The gated deploy is a separate, deliberate step.

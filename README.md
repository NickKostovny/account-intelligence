# Account intelligence

Site-level intelligence for Invert's account executives: one brief per target account, with a verdict for every site, the named site head, what the site does and makes, hiring, news and publications. Built July to October 2026 by Nick Kostovny with Claude Code. This repo is the runnable whole. Five companion repos explain each stage front to back.

| Where | Link | Access |
|---|---|---|
| Live board | https://account-intel-invert.vercel.app | Invert Vercel team SSO, or Nick's share link |
| Portable handoff (every script, rules, gotchas, in one file) | https://claude.ai/artifact/YHJWpLrao3VXazm9rtxJkf | private until shared |
| Demo walkthrough | [DEMO-WALKTHROUGH.md](DEMO-WALKTHROUGH.md) | this repo |
| Internal handoff, full history | [HANDOFF.md](HANDOFF.md) | this repo |
| Table schemas, header rows only | [TABLES.md](TABLES.md) | this repo |

**No data is in any repo.** `data/` holds real accounts, named people, deal status and inferred hypotheses. It stays on Nick's Mac or travels as a zip to the next person. Without it the scripts run and the briefs render empty. See [Data](#data).

## The stages, in pipeline order

| # | Repo | What it fills on the brief | Entry scripts |
|---|---|---|---|
| 1 | [account-intel-core](https://github.com/NickKostovny/account-intel-core) | the four base tables: accounts, sites, people, signals | `normalize.py`, `merge.py`, `aiq.py` (shared library) |
| 2 | [account-intel-job-corpus](https://github.com/NickKostovny/account-intel-job-corpus) | Hiring column, Reqs, Units, Coverage, the org map, digital/IT location evidence | `ats_probe.py`, `jobs_cdx.py`, `jobs_workday.py`, `fetch_bodies.py`, `gen_org_wf.py`, `verify_org.py`, `merge_org.py`, `build_org.py` |
| 3 | [account-intel-site-heads](https://github.com/NickKostovny/account-intel-site-heads) | Named site head column: LinkedIn-verified person, title, email | `clay_targets.py`, `merge_clay.py`, `merge_roles.py` |
| 4 | [account-intel-jev-layers](https://github.com/NickKostovny/account-intel-jev-layers) | Verdict and Fit columns, Functions and Modality chips, posting labels, News column, publication relevance | `jev_site_fit.py`, `jev_site_final.py`, `jev_site_profile.py`, `jev_verify_sources.py`, `jev_postings.py`, `jev_digital_it.py`, `news_collect.py`, `jev_news.py` |
| 5 | [account-intel-briefs](https://github.com/NickKostovny/account-intel-briefs) | the pages themselves and the gated deploy | `build-briefs.py`, `stage_deploy.py`, `orgmap.py` |

Each companion repo carries byte-identical copies of the shared modules it imports (`aiq.py`, `normalize.py`, `orgmap.py`). Change them here and copy them over; the companion repos are for reading and sharing one stage at a time. The code in this repo runs as one folder because every script expects its siblings next to it.

## How a site-level data point gets filled

1. **Source of truth.** The US and EU site-mapping sheets and the Top-50 account list go through `normalize.py` into `data/accounts.csv`, `sites.csv`, `people.csv`, `signals.csv`. `merge.py` folds in the first enrichment run (publications, talks, news). Edit site facts in the sources, never in `briefs/`.
2. **Site verdict.** Three readings in order of trust. The policy map (Claude's site map under Nick's rules). The Jev reading (`jev_site_fit.py`: ten atomic questions per site, combined in code). The source check where the two disagree (`jev_verify_sources.py`, `jev_check_merge.py`). `jev_site_final.py` writes one verdict per site with its basis.
3. **Named site head.** `clay_targets.py` pulls names from the site-map prose, Clay verifies them on LinkedIn in 20-name batches, `merge_clay.py` writes `data/site_heads.csv`.
4. **Functions and modality.** `jev_site_profile.py`.
5. **Hiring and digital/IT.** The job-postings corpus: two Tier-1 collectors (Wayback CDX, Workday API), Jev labels, Tier-2 org extraction gated by exact-quote verification.
6. **News.** `news_collect.py`, `jev_news.py`.
7. **Publications.** Europe PMC pulls, banded by `jev_publications.py`.
8. **Build, gate, deploy.** `build-briefs.py`, `stage_deploy.py`, a Vercel preview, an alias, `stage_deploy.py --check-all`.

The full command sequence is in [HANDOFF.md §7](HANDOFF.md) and in each companion README.

## Timeline

| Date | What landed |
|---|---|
| 2026-07-23 | Four-table SSOT, first briefs, enrichment workflow (55 agents). Job postings were buying signals. |
| 2026-07-29 | Reframe: postings are a context corpus for org structure. Two Tier-1 collectors, Tier-2 extraction with a verbatim quote gate (403 of 403), org map as the brief's primary view, MCP server with structured records only. |
| 2026-07-30 | Brief reordered after Nick's cold read: publications up, hiring table into a fold. |
| 2026-09-10 | Sales-facing pass: briefs lead with sites and named heads; Clay-verified site heads (189 checked, 147 on LinkedIn); sweep widened; gated deploy moved to the Invert Vercel team; the `--prod` incident and the rule that followed. |
| 2026-09-28 | Jev (TypeSafe) pilot: typed answers at cents per thousand sites. Site verdict policy decided. Five Jev layers built locally. |
| 2026-09-30 | Automated source check replaces the human label pass. All 604 sites get a verdict basis. News collector. Animal health is KEEP. Deployed. |
| 2026-10-06 | This handoff. |

## Rules that must not drift

- Never `vercel --prod`. Preview, alias, `stage_deploy.py --check-all`.
- Never share `account-intel.vercel.app` (a third party) or `account-intel-two.vercel.app`.
- Never a public URL for the briefs.
- No explanatory text on the page.
- Never run two writers at once. `aiq.lock()` wraps every read-modify-write.
- No org edge without a verified quote. The build asserts zero `<p>` tags.
- Never chart raw requisition counts from Wayback; use `share` and `peer_index`.
- No people data to TypeSafe without Nick's explicit approval.
- HubSpot write-back stays gated.
- Python 3.9, standard library only.

## Data

`data/` is ignored by git. Supply it as a zip (about 30 MB without `data/cache/`, 166 MB with it) or work on Nick's Mac at `~/Website Marketing Landing Pages/account-intelligence/`. Four durable files sit one folder up: `enrich_out.json`, `org_out_20260729.json`, `org_out_biogen.json`, `org_stage0.json`. `enrich.wf.js` is ignored too because its embedded input names people; `enrich.wf.template.js` is the same workflow with that block stripped. The TypeSafe key lives in the macOS Keychain under `TYPESAFE_API_KEY`.

## Requirements

python3 3.9 or later, no packages. Network steps need the sandbox off. The Vercel CLI at `/opt/homebrew/bin/vercel`, logged in to the `invert` team. Clay through the Clay MCP, workspace 1243060.

## Start here if you are continuing the work

1. Read [DEMO-WALKTHROUGH.md](DEMO-WALKTHROUGH.md), then [HANDOFF.md §0 and §10](HANDOFF.md).
2. Get the data in place and run `python3 build-briefs.py`; open `briefs/index.html`.
3. Pick from the open items in HANDOFF.md §8 and §10. The largest: 26 sites need a person; PENDING-CLAY site heads for Eli Lilly and Novo Nordisk; collectors for SuccessFactors and Eightfold.

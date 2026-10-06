# Account intelligence board. Demo walkthrough

Written 2026-10-06 from `HANDOFF.md` §0 and §10. The board build is dated 2026-09-30. Every step below has run.

## Open the board

| Where | URL | Access |
|---|---|---|
| Local (this Mac) | http://localhost:8830 | launch config `account-intel` |
| Live | https://account-intel-invert.vercel.app | Invert Vercel team SSO |
| Live, no login | the `_vercel_share` link Nick created 2026-09-10 | anyone who holds the link |

Never share `account-intel.vercel.app` (a third party's site) or `account-intel-two.vercel.app` (removed). The data is confidential: real accounts, named people, deal status.

## Click path for the demo

1. **Index.** Accounts group as Live pipeline, No deal yet, Closed lost. Each card shows status, owner, KEEP sites, named heads, LinkedIn matches, publications.
2. **Open AstraZeneca.** Stat chips first. Then the Sites table, KEEP first.
3. **Sites table columns.** Site, City, Verdict, Fit, Named site head, Functions, Modality, Hiring, News. Reqs, Units and Coverage appear only on swept accounts.
4. **Fit cell.** Verdict, reason tag, source link. Hover shows the basis: readings agree, checked, or needs person.
5. **Named site head.** LinkedIn link, current title from Clay, "matched on LinkedIn <date>". The site-map prose is the fallback.
6. **Scroll.** Publications, then the posting layer (org map, folded unless the account has org units), lineage, hiring fold.
7. **Rule on the page.** No explanatory text. Tables, noun titles, one summary line. Method notes live in `HANDOFF.md`.

## Workflow: how each site-level data point gets filled

All scripts are python3, no dependencies, re-runnable. Run from `account-intelligence/`. Never run two writers at once.

### Step 1. Source of truth

Four CSVs in `data/`: accounts, sites, people, signals. Sources are the US and EU site-mapping sheets and the Top-50 list.

```bash
python3 normalize.py
```

Edit site facts in the source sheets, never in `briefs/`.

### Step 2. Site verdict (the Fit column)

Three readings, in order of trust.

1. **Policy map.** Claude's site map under Nick's policy: small-molecule-only or mixed → FLAG, R&D-only → FLAG, CDMO → KEEP, animal health → KEEP.
2. **Jev reading.** Ten atomic questions per site. Code combines them into KEEP, FLAG, GAP, DROP, CLOSED or REVIEW plus a reason tag. Site text only leaves the machine, names scrubbed.

```bash
python3 jev_site_fit.py
```

3. **Source check** where the two readings disagree, or the verdict is REVIEW. An evidence workflow finds public pages. Code keeps only verbatim quotes. Jev re-asks the questions from the quotes. Claude adjudicates each disagreement and a second agent tries to refute it.

```bash
python3 jev_verify_sources.py <evidence.json> <run_name>
python3 jev_check_merge.py <run_name>
python3 jev_site_final.py
```

Output: `data/jev/site_verdict_final.csv`, one verdict per site with its basis.

| Basis | Sites |
|---|---|
| Readings agree | 377 |
| Checked against a source | 201 |
| Needs a person | 26 |

### Step 3. Named site head (Clay-verified)

```bash
python3 clay_targets.py        # names from the site-map prose on KEEP sites, 20-name batches
# subagents: Clay search-contacts-by-name per batch, then add-contact-data-points Email
python3 merge_clay.py          # -> data/site_heads.csv
```

Open: PENDING-CLAY heads for Eli Lilly (11 sites) and Novo Nordisk (15 sites). Recipe is role search per account, then `merge_roles.py`.

### Step 4. Functions and Modality chips

```bash
python3 jev_site_profile.py all   # -> site_profile.csv, account_profile.csv
```

Bare "CGT" gives both chips. Specific mentions keep the Jev split.

### Step 5. Hiring and Digital/IT

```bash
python3 ats_probe.py              # careers hosts (cached)
python3 jobs_cdx.py --all         # Wayback collector
python3 jobs_workday.py --all     # Workday collector
python3 jobs_cdx.py --rollup
python3 jev_postings.py           # job family, digital, bioprocess, seniority labels
python3 jev_digital_it.py all     # where digital/IT hiring sits
```

No collector yet for SuccessFactors or Eightfold. The org map (Tier 2) exists for AstraZeneca and Biogen only.

### Step 6. News

```bash
python3 news_collect.py           # Google News RSS, cached; --refresh to refetch
python3 jev_news.py               # -> data/news/*.csv
```

### Step 7. Publications

Europe PMC pulls come from the enrichment workflow. `merge.py` folds them in. `jev_publications.py` sets the relevance band.

### Step 8. Build, gate, deploy

```bash
python3 build-briefs.py
python3 stage_deploy.py
cd deploy && /opt/homebrew/bin/vercel deploy --yes --scope invert
/opt/homebrew/bin/vercel alias set <hash-url> account-intel-invert.vercel.app --scope invert
cd .. && python3 stage_deploy.py --check-all
```

Deploy a preview. Never `--prod`. The check reads the full body and fails unless the alias lands on SSO and `-two` returns 404.

## Open items to state if asked

- 26 sites need a person. Examples: Pfizer Sanford, Lonza Lexington, Sanofi Orlando.
- PENDING-CLAY site heads for Eli Lilly and Novo Nordisk.
- No SuccessFactors or Eightfold collector.
- Monthly refresh is not armed. Biweekly Tier-1 index task is armed.
- HubSpot write-back stays gated.
- Persona check on people data needs approval before Jev sees it.

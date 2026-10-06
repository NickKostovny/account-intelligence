---
name: ai-jobs-index-biweekly
description: Biweekly Tier-1 refresh of the account-intelligence job-posting corpus (deterministic Python only, no LLM agents, no workflow)
---

Refresh the Tier-1 job-requisition corpus for Invert's account-intelligence system. This run is DETERMINISTIC PYTHON ONLY — do not run the Workflow tool, do not spawn agents, do not call an LLM to extract anything.

Working directory: /Users/nickkostovny/Website Marketing Landing Pages/account-intelligence

Read HANDOFF.md first for context. Network steps need Bash with dangerouslyDisableSandbox: true, and python3 is 3.9 (stdlib only).

CRITICAL: never run two of these scripts concurrently. They each do read-modify-write on data/postings.csv and hold an advisory lock; running them in parallel previously destroyed 203 rows. Run strictly in order, one at a time, waiting for each to finish:

  1. python3 ats_probe.py --refresh-stale
  2. python3 jobs_cdx.py --all
  3. python3 jobs_workday.py --all
  4. python3 jobs_cdx.py --rollup
  5. python3 build_org.py
  6. python3 build-briefs.py

Then report, concisely:
  - total rows in data/postings.csv, and how many are NEW this run
  - the 15 highest-scoring new requisitions (role_title | city | account | tier2_score), and how many of those are newly tier2_admit=true and therefore queued for the next extraction run
  - any account whose collector returned ZERO requisitions this cycle — that usually means the careers host changed and needs `python3 ats_probe.py --account <aid>` or, for a Workday tenant, `python3 jobs_workday.py --discover <aid>`
  - any account still at probe_status=needs_discovery (run `python3 ats_probe.py --report`)
  - the site coverage state counts from build_org.py

Rules:
  - Do NOT edit any file outside account-intelligence/data/ and account-intelligence/briefs/.
  - Do NOT hand-edit any CSV. Do NOT touch org_overrides.json — that is human judgement.
  - Do NOT deploy anything. The Vercel deploy is a separate, deliberately manual step.
  - If any script errors, STOP and report the traceback verbatim rather than continuing.
  - Requisition capture dates from the Wayback collector are FIRST-CAPTURE dates, not posted dates. Never present a raw per-quarter requisition count as hiring volume; if you report a trend, use the share/peer_index columns in data/posting_rollup.csv and quote the bias_note.
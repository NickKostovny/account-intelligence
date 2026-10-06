#!/usr/bin/env python3
"""
Generate a Workflow script that extracts org structure from cached requisition bodies.

The agents do NOT fetch anything. fetch_bodies.py already holds the bytes, so each shard's prompt
carries the body text inline and the agent's only job is to read it and emit structure. Three
consequences, all of which came out of Stage 0:

  * Quote verification becomes an exact substring test against our own cached copy
    (verify_org.py), instead of trusting what a model reports it read.
  * No tokens are burned on dead URLs -- Stage 0 spent most of one agent's turns discovering that
    4 of 6 reqs were delisted.
  * Runs are reproducible: same bodies in, same extraction target out.

  python3 gen_org_wf.py --account astrazeneca --shard 8 --out wf/org-az.wf.js
  python3 gen_org_wf.py --pending --max-agents 10 --out wf/org-refresh.wf.js

Then: Workflow({scriptPath: "<abs>/wf/org-az.wf.js"})
      save .result to ../org_out_<date>.json
      python3 verify_org.py ../org_out_<date>.json && python3 merge_org.py ../org_out_<date>.json
"""
import argparse, json, os, re

import aiq
from fetch_bodies import BODIES, MIN_BODY

BODY_CAP = 9000          # a job description fits; keeps a shard prompt near ~60k chars


def body_of(pid):
    p = os.path.join(BODIES, pid + ".txt")
    if not os.path.exists(p):
        return ""
    t = open(p, encoding="utf-8").read()
    return t[:BODY_CAP]


def targets(accounts, pending_only, min_score):
    rows = aiq.load("postings.csv")
    done = {r["posting_id"] for r in aiq.load("posting_extracts.csv")}
    out = []
    for r in rows:
        if r["tier2_admit"] != "true":
            continue
        if accounts and r["account_id"] not in accounts:
            continue
        if int(r["tier2_score"] or 0) < min_score:
            continue
        if pending_only and r["posting_id"] in done:
            continue
        b = body_of(r["posting_id"])
        if len(b) < MIN_BODY:
            continue
        out.append((r, b))
    out.sort(key=lambda x: -int(x[0]["tier2_score"] or 0))
    return out


SCHEMA = r'''
const ORG = {
  type: 'object', additionalProperties: false,
  properties: {
    postings: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: {
        posting_id: { type: 'string' },
        team_name: { type: 'string' },
        team_acronym: { type: 'string' },
        parent_org: { type: 'string' },
        reports_to_title: { type: 'string' },
        hiring_manager: { type: 'string' },
        site_stated: { type: 'string' },
        posted_date: { type: 'string' },
        autonomy_flag: { type: 'string', enum: ['', 'site_autonomous', 'central_owned', 'mixed'] },
        acquisition_flag: { type: 'string' },
        supports_teams: { type: 'array', items: {
          type: 'object', additionalProperties: false,
          properties: { team: { type: 'string' }, verbatim: { type: 'string' } },
          required: ['team', 'verbatim'] } },
        tech_stack: { type: 'array', items: {
          type: 'object', additionalProperties: false,
          properties: { tool: { type: 'string' }, verbatim: { type: 'string' } },
          required: ['tool', 'verbatim'] } },
        vocabulary: { type: 'array', items: { type: 'string' } },
        quotes: { type: 'array', items: {
          type: 'object', additionalProperties: false,
          properties: {
            claim_type: { type: 'string', enum: ['team_name','team_acronym','parent_org',
              'supports_team','reports_to','hiring_manager','site_stated','tech','vocabulary',
              'pain','autonomy','acquisition','posted_date'] },
            subject: { type: 'string' },
            quote_text: { type: 'string' } },
          required: ['claim_type','subject','quote_text'] } },
        notes: { type: 'string' },
      },
      required: ['posting_id','team_name','team_acronym','parent_org','reports_to_title',
        'hiring_manager','site_stated','posted_date','autonomy_flag','acquisition_flag',
        'supports_teams','tech_stack','vocabulary','quotes','notes'],
    } },
    notes: { type: 'string' },
  },
  required: ['postings', 'notes'],
}
'''

RULES = r'''const RULES = `You are reading the FULL TEXT of real job requisitions, supplied below. Do not fetch anything; everything you need is in the text. Your output is data for a database that drives customer-facing messaging, so a fabricated team name or quote is worse than an empty field.

FRAMING. These postings are not buying signals. They are a context corpus for reconstructing a company's internal org structure, which is invisible on LinkedIn -- people do not put their department, their team's real internal name, or their reporting line in a LinkedIn title, but a posting has to describe where the role sits in order to hire for it. The org-structural detail IS the product here.

HARD RULES.
1. Every value must come from the supplied text. No inference from background knowledge about the company, no completion of a partial name, no guessing an acronym.
2. For EVERY non-empty field you emit -- team_name, team_acronym, parent_org, reports_to_title, hiring_manager, site_stated, posted_date, every supports_teams[] entry, every tech_stack[] entry -- add a matching quotes[] entry whose quote_text is copied CHARACTER-FOR-CHARACTER from the supplied text. Same words, same order, same capitalisation, same punctuation. Do not paraphrase, tidy, re-wrap, translate, or fix the source's typos or spacing. A deterministic script asserts each quote_text is a literal substring of the body and QUARANTINES every claim whose quote fails, so a near-miss quote silently destroys the claim it was meant to support.
3. Keep each quote_text between about 8 and 40 words.
4. If something is not stated, return an empty string or an empty array. Empty is a correct, expected answer and renders as a visible gap. Never fill a field just because it exists in the schema. Most postings name no hiring manager and give no acronym.
5. team_name is the team's real internal name as the posting words it (e.g. "Process Data Science and Statistics"), not the generic function and not the job title. team_acronym ONLY if the posting spells it out in parentheses or defines it.
6. parent_org is the organisation the team sits INSIDE, one level up. Look for "within", "part of", "sits in", "reports into", "member of". Do NOT record a corporate-boilerplate parent: "part of the AstraZeneca group" is marketing copy about the whole company, not this team's parent org. If the only candidate is the company itself, leave parent_org empty.
7. supports_teams are OTHER named teams this role serves or partners with -- lateral relationships. Skip generic words like "stakeholders" or "the business".
8. tech_stack is every named system, platform, tool or language: Snowflake, Streamlit, Databricks, Python, R, SQL, JMP, SIMCA, Unicorn, OSI PI, OpenLab, Empower, Genedata, Discoverant, Benchling, MES, LIMS, ELN, QMS, ERP, SAP, PLC, SCADA, historian, PAT, AWS, Azure, and so on. "Empowering" is not Empower and "R&D" is not the language R -- match the tool, not the substring.
9. vocabulary: 2-5 verbatim phrases in which the team describes its OWN work and its own problems. These feed messaging, so they must be the team's exact words.
10. autonomy_flag: site_autonomous if the posting shows this site deciding its own tooling; central_owned if a global or central function owns it; mixed if both; empty if the text does not say. Support it with an autonomy quote.
11. posted_date: only if the text prints one. Convert to YYYY-MM-DD. Never substitute today's date or a guess.`
'''


def gen(shards, account_label):
    js = []
    js.append("// GENERATED by gen_org_wf.py -- do not hand-edit; regenerate instead.\n"
              "// Tier 2 org-structure extraction. Bodies are EMBEDDED (fetch_bodies.py already\n"
              "// cached them), so agents never touch the network and every quote can be verified\n"
              "// as an exact substring of our own copy by verify_org.py.\n"
              "//\n"
              "// Input is embedded as `const DATA`. Passing it via the Workflow `args` param\n"
              "// arrives undefined and kills the run -- verified the hard way, see HANDOFF.md.\n")
    js.append("export const meta = {")
    js.append("  name: 'org-extract-%s'," % account_label)
    js.append("  description: 'Extract org structure (team, parent, supports, stack, vocabulary) "
              "from %d cached job-requisition bodies'," % sum(len(s) for s in shards))
    js.append("  phases: [{ title: 'Extract', detail: '%d shards, %d requisitions' }],"
              % (len(shards), sum(len(s) for s in shards)))
    js.append("}\n")
    js.append("const DATA = " + json.dumps(
        [[{"posting_id": r["posting_id"], "account_id": r["account_id"],
           "role_title": r["role_title"], "city": r["city_raw"], "url": r["url"],
           "body": b} for r, b in shard] for shard in shards],
        separators=(",", ":")) + "\n")
    js.append(SCHEMA)
    js.append(RULES)
    js.append(r'''
function shardPrompt(shard) {
  return RULES + "\n\nExtract from these " + shard.length + " requisitions. Return one postings[] entry per requisition, echoing posting_id exactly.\n\n" +
    shard.map(function (p, i) {
      return "=== REQUISITION " + (i + 1) + " ===\nposting_id: " + p.posting_id +
             "\nrole_title (from the URL slug, may be imprecise): " + p.role_title +
             "\ncity (from the URL): " + p.city +
             "\nsource: " + p.url +
             "\n--- BODY TEXT BEGINS ---\n" + p.body + "\n--- BODY TEXT ENDS ---\n";
    }).join("\n") +
    "\nSet the top-level notes to: which fields were systematically absent across this shard, and any requisition whose body looked truncated or was mostly site chrome.";
}

phase('Extract')
log('org extraction: ' + DATA.length + ' shards, ' + DATA.reduce(function (n, s) { return n + s.length }, 0) + ' requisitions, no network')

const results = await parallel(DATA.map(function (shard, i) {
  return function () {
    return agent(shardPrompt(shard), {
      label: 'org:shard' + (i + 1),
      phase: 'Extract',
      schema: ORG,
    })
  }
}))

const postings = []
const notes = []
results.filter(Boolean).forEach(function (r, i) {
  (r.postings || []).forEach(function (p) { postings.push(p) })
  if (r.notes) notes.push('shard' + (i + 1) + ': ' + r.notes)
})
return { postings: postings, notes: notes.join('\n'), shards: DATA.length,
         requested: DATA.reduce(function (n, s) { return n + s.length }, 0) }
''')
    return "\n".join(js)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", action="append")
    ap.add_argument("--shard", type=int, default=8)
    ap.add_argument("--max-agents", type=int, default=12)
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    tg = targets(set(a.account) if a.account else None, a.pending, a.min_score)
    if not tg:
        print("nothing to extract: no admitted requisition has a cached body >= %d chars.\n"
              "Run fetch_bodies.py first." % MIN_BODY)
        return
    cap = a.shard * a.max_agents
    dropped = 0
    if len(tg) > cap:
        dropped = len(tg) - cap
        tg = tg[:cap]
    shards = [tg[i:i + a.shard] for i in range(0, len(tg), a.shard)]

    label = "-".join(sorted(a.account)) if a.account else "pending"
    out = a.out if os.path.isabs(a.out) else os.path.join(aiq.HERE, a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(gen(shards, re.sub(r"[^a-z0-9-]", "", label.lower())))

    chars = sum(len(b) for _r, b in tg)
    print("wrote %s" % out)
    print("  %d requisitions in %d shards (%d agents, cap %d)" % (len(tg), len(shards), len(shards), a.max_agents))
    print("  embedded body text: %s chars (~%dk input tokens)" % (format(chars, ","), chars // 3500))
    if dropped:
        # Never truncate silently -- a capped run that reads like full coverage is the failure mode.
        print("  DROPPED %d lower-scoring requisitions to stay inside --max-agents %d."
              % (dropped, a.max_agents))
        print("  Re-run with --pending after merging to pick them up.")


if __name__ == "__main__":
    main()

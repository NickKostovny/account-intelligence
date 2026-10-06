// Invert account-intelligence enrichment workflow (PROVEN — 55 agents, 0 errors, 148K tokens, ~6.6 min).
// Run via the Workflow tool: Workflow({scriptPath: "<abs path to this file>"})
// Output: save the tool result's `.result` object to enrich_out.json, then:
//   python3 normalize.py && python3 merge.py ../enrich_out.json && python3 build-briefs.py
//
// CRITICAL LESSON: input data is EMBEDDED below as `const DATA`, NOT passed via the Workflow `args`
// param. Passing a large object through `args` arrived as undefined (`args.pilot` threw). Embedding works.
//
// PHASE 2 TODO: the 5 "green" field-group agents (lineage, news, conference, jobs) currently run for the
// PILOT ONLY. To roll them to all 49 accounts, wrap those prompts in a per-account map like pubThunks does,
// and generalize the hardcoded "AstraZeneca" strings + cityList to the account being processed.
// Watch the concurrency cap (min(16, cores-2)) and the medium workflow-size guideline.

export const meta = {
  name: 'account-intel-enrich',
  description: 'Track A: deep-enrich pilot account (AstraZeneca) across all fields. Track B: publications for all 49 accounts.',
  phases: [
    { title: 'Pilot', detail: 'AstraZeneca: lineage, news, pubs, conference, jobs, decision-power' },
    { title: 'Publications', detail: 'Europe PMC affiliation pulls for all 49 active accounts' },
  ],
}

// ---------- embedded input ----------
// Regenerate this block from the SSOT with:
//   python3 -c "import csv,json; ..."  (see HANDOFF.md "Regenerating the workflow input")
const DATA = {"pilot": null, "accounts": []};  // STRIPPED FOR THE REPO. The real block embeds site rows and named people from data/. Regenerate it from the SSOT (see README: "Regenerating the workflow input").
"accounts":[{"id":"abbvie","company":"AbbVie"},{"id":"agc-biologics","company":"AGC Biologics"},{"id":"alexion","company":"Alexion"},{"id":"alnylam","company":"Alnylam"},{"id":"amgen","company":"Amgen"},{"id":"ascendis-pharma","company":"Ascendis Pharma"},{"id":"astellas","company":"Astellas"},{"id":"astrazeneca","company":"AstraZeneca"},{"id":"bayer","company":"Bayer"},{"id":"beigene","company":"BeiGene"},{"id":"biogen","company":"Biogen"},{"id":"biomarin","company":"BioMarin"},{"id":"biontech","company":"BioNTech"},{"id":"boehringer-ingelheim","company":"Boehringer Ingelheim"},{"id":"bristol-myers-squibb","company":"Bristol Myers Squibb"},{"id":"chugai","company":"Chugai"},{"id":"csl","company":"CSL"},{"id":"daiichi-sankyo","company":"Daiichi Sankyo"},{"id":"eisai","company":"Eisai"},{"id":"eli-lilly","company":"Eli Lilly"},{"id":"fujifilm","company":"Fujifilm"},{"id":"genentech","company":"Genentech"},{"id":"genmab","company":"Genmab"},{"id":"gilead-sciences","company":"Gilead Sciences"},{"id":"gsk","company":"GSK"},{"id":"incyte","company":"Incyte"},{"id":"ipsen","company":"Ipsen"},{"id":"jazz-pharmaceuticals","company":"Jazz Pharmaceuticals"},{"id":"johnson-johnson","company":"Johnson & Johnson"},{"id":"lonza","company":"Lonza"},{"id":"lundbeck","company":"Lundbeck"},{"id":"merck-and-co","company":"Merck and Co"},{"id":"merck-kgaa","company":"Merck KGaA"},{"id":"moderna","company":"Moderna"},{"id":"novartis","company":"Novartis"},{"id":"novavax","company":"Novavax"},{"id":"novo-nordisk","company":"Novo Nordisk"},{"id":"otsuka","company":"Otsuka"},{"id":"pfizer","company":"Pfizer"},{"id":"recipharm","company":"Recipharm"},{"id":"regeneron","company":"Regeneron"},{"id":"roche","company":"Roche"},{"id":"samsung-biologics","company":"Samsung Biologics"},{"id":"sanofi","company":"Sanofi"},{"id":"seagen","company":"Seagen"},{"id":"takeda","company":"Takeda"},{"id":"ucb","company":"UCB"},{"id":"vertex","company":"Vertex"},{"id":"zoetis","company":"Zoetis"}]}

// ---------- schemas ----------
const PUBS = {
  type: 'object', additionalProperties: false,
  properties: {
    publications: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: {
        title: { type: 'string' }, year: { type: 'string' }, journal: { type: 'string' },
        authors_named: { type: 'array', items: { type: 'string' } },
        affiliation_string: { type: 'string' }, matched_city: { type: 'string' },
        topic: { type: 'string' }, pain_hypothesis: { type: 'string' },
        url: { type: 'string' }, source: { type: 'string' },
        confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
      },
      required: ['title', 'year', 'authors_named', 'affiliation_string', 'matched_city', 'topic', 'pain_hypothesis', 'url', 'source', 'confidence'],
    } },
    notes: { type: 'string' },
  },
  required: ['publications', 'notes'],
}
const SIGNALS = {
  type: 'object', additionalProperties: false,
  properties: {
    signals: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: {
        signal_type: { type: 'string' }, title: { type: 'string' }, date: { type: 'string' },
        named_people: { type: 'array', items: { type: 'string' } },
        matched_city: { type: 'string' }, topic: { type: 'string' },
        pain_hypothesis: { type: 'string' }, url: { type: 'string' },
        source: { type: 'string' }, confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
      },
      required: ['signal_type', 'title', 'date', 'named_people', 'matched_city', 'topic', 'pain_hypothesis', 'url', 'source', 'confidence'],
    } },
    notes: { type: 'string' },
  },
  required: ['signals', 'notes'],
}
const JOBS = {
  type: 'object', additionalProperties: false,
  properties: {
    postings: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: {
        title: { type: 'string' }, dept: { type: 'string' }, seniority: { type: 'string' },
        location: { type: 'string' }, matched_city: { type: 'string' }, posted_date: { type: 'string' },
        tools_named: { type: 'array', items: { type: 'string' } },
        url: { type: 'string' }, source: { type: 'string' },
      },
      required: ['title', 'dept', 'seniority', 'location', 'matched_city', 'posted_date', 'tools_named', 'url', 'source'],
    } },
    digital_data_it_proxy: { type: 'string' },
    digital_data_it_confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
    notes: { type: 'string' },
  },
  required: ['postings', 'digital_data_it_proxy', 'digital_data_it_confidence', 'notes'],
}
const LINEAGE = {
  type: 'object', additionalProperties: false,
  properties: {
    lineage_summary: { type: 'string' },
    acquisitions: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: { acquired: { type: 'string' }, year: { type: 'string' }, site_city: { type: 'string' }, note: { type: 'string' }, url: { type: 'string' } },
      required: ['acquired', 'year', 'site_city', 'note', 'url'],
    } },
    site_legal_entities: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: { matched_city: { type: 'string' }, legal_entity: { type: 'string' }, url: { type: 'string' } },
      required: ['matched_city', 'legal_entity', 'url'],
    } },
    modality_footprint: { type: 'string' },
    sources: { type: 'array', items: { type: 'string' } },
  },
  required: ['lineage_summary', 'acquisitions', 'site_legal_entities', 'modality_footprint', 'sources'],
}
const DECISION = {
  type: 'object', additionalProperties: false,
  properties: {
    it_centralization_hypothesis: { type: 'string' },
    it_centralization_confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
    digital_data_it_location: { type: 'string' },
    decision_power_hypothesis: { type: 'string' },
    decision_power_confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
    evidence: { type: 'array', items: { type: 'string' } },
    sources: { type: 'array', items: { type: 'string' } },
  },
  required: ['it_centralization_hypothesis', 'it_centralization_confidence', 'digital_data_it_location', 'decision_power_hypothesis', 'decision_power_confidence', 'evidence', 'sources'],
}

const A = DATA.pilot
const ACCTS = DATA.accounts
const cityList = A.sites.map(s => s.city).filter(Boolean).join('; ')

const NOFAB = 'CRITICAL: every record MUST be a real, verifiable item with a working URL. Do NOT invent titles, authors, dates, numbers, DOIs, or URLs. Leave a field blank rather than guess. If you find nothing verifiable, return an empty array and explain in notes. Your output is data for a database, not a message to a person.'

function pubPrompt(company, cities) {
  return `Gather RECENT (2024-2026) bioprocess-relevant scientific publications authored by employees of ${company}, for a B2B sales-intelligence dataset. ${NOFAB}

Method:
1. Query the Europe PMC REST API via WebFetch (URL-encode the company name; & becomes %26, spaces %20):
   https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=AFF:%22${encodeURIComponent(company)}%22%20AND%20(PUB_YEAR:2024%20OR%20PUB_YEAR:2025%20OR%20PUB_YEAR:2026)&format=json&pageSize=50&resultType=core
   Ask WebFetch to extract, per result: title, authorString, each author's affiliation string, journalTitle, pubYear, firstPublicationDate, doi, pmid. If the first fetch is thin, also try a WebSearch for "${company}" bioprocessing/process development publications 2024 2025.
2. KEEP ONLY publications where an author affiliation string EXPLICITLY contains "${company}" (guard against same-name false positives and academic authors merely citing the company). Put the verbatim affiliation in affiliation_string; extract its city into matched_city${cities ? ' (match against these known site cities when possible: ' + cities + ')' : ''}.
3. Prioritize bioprocess topics: process development, cell culture/upstream, downstream/purification, CMC, analytical development, formulation, MSAT, manufacturing science, bioreactor, chromatography, host-cell protein, aggregation, PAT, digital/AI in bioprocessing. De-prioritize pure clinical/discovery papers.
4. Per kept publication: authors_named = the author(s) affiliated with ${company} (potential champions/SMEs); topic = the technical workflow; pain_hypothesis = one sentence on the likely bioprocess data/analytics pain implied; url = https://doi.org/<doi> or the Europe PMC record URL; source = "Europe PMC"; confidence = high (company affiliation + clear bioprocess topic), medium (affiliation match, ambiguous topic), or low.
5. Return at most the 15 most relevant, most recent. Set notes to coverage caveats (e.g. affiliation ambiguity, API thinness).`
}

// ---------- Track A: pilot field groups ----------
const pilotThunks = [
  () => agent(
    `Compile AstraZeneca's acquired-company lineage and site legal entities relevant to bioprocessing. ${NOFAB}
Cover major acquisitions that live on as sites/organizations: MedImmune (Gaithersburg biologics), Alexion (New Haven/Boston rare disease), Gracell (Tarzana cell therapy), Neogene Therapeutics, Amolyt, CinCor, TeneoBio, Caelum, Synageva (Bogart transgenics), Definiens, Spirogen. For each: acquired, year, the site/city it maps to, a one-line note on the organizational DNA it carries, and a real url (company press, Wikipedia, SEC/10-K, reputable news). Also give site_legal_entities (matched_city + legal entity where findable) and modality_footprint (all modalities AZ works across, company-wide). Map cities against: ${cityList}.`,
    { label: 'pilot:lineage', phase: 'Pilot', schema: LINEAGE }),

  () => agent(
    `Find real, recent (roughly the last 12 months, 2025-2026) company- and site-level news for AstraZeneca relevant to bioprocessing GTM: M&A/acquisitions, biologics/cell/gene-therapy clinical readouts and approvals, plant investments/capex/expansions/closures, tech transfers, and site openings. Use WebSearch + WebFetch on reputable sources (AstraZeneca press releases, FiercePharma, Endpoints News, BioProcess International, Reuters). ${NOFAB}
Per item: signal_type is one of ma_news | approval | capex | program_news; title; date; named_people (executives/scientists named, if any); matched_city (attribute to a site when a location is named, against: ${cityList}); topic; pain_hypothesis (one sentence linking the news to a bioprocess data/analytics opportunity); url; source; confidence. Return up to 20.`,
    { label: 'pilot:news', phase: 'Pilot', schema: SIGNALS }),

  () => agent(pubPrompt('AstraZeneca', cityList), { label: 'pilot:pubs', phase: 'Pilot', schema: PUBS }),

  () => agent(
    `Find real conference talks/presentations/posters given by AstraZeneca employees at bioprocessing / CMC / analytical / bio-IT conferences in 2024-2026 (e.g. BioProcess International/BPI West, PEGS Boston, The Bioprocessing Summit, ISPE, Interphex, Cell & Gene Therapy, Well Characterized Biologics, Bio-IT World, Informa/KNECT365 events). Use WebSearch + WebFetch on conference agendas/programs. ${NOFAB}
The model example: a "pDADMAC" talk reveals host-cell-protein clearance / flocculation work and its data pain. Per talk: signal_type = "conference_talk"; title; date; named_people (speaker names); matched_city (speaker's site, against: ${cityList}); topic (the technical workflow the talk reveals); pain_hypothesis (the likely bioprocess pain/workflow, one sentence); url (agenda/abstract); source (conference name); confidence. Return up to 15; note gaps in notes if agendas are inaccessible.`,
    { label: 'pilot:conf', phase: 'Pilot', schema: SIGNALS }),

  () => agent(
    `Find CURRENT job postings at AstraZeneca that reveal bioprocessing org structure and tooling. Focus on process development, MSAT, CMC, analytical, manufacturing sciences, and especially data/digital/IT roles within those functions (data engineer, bioprocess data scientist, digital manufacturing, MES/LIMS, automation, AI/ML). Use WebSearch + WebFetch (careers.astrazeneca.com, LinkedIn Jobs, Indeed). ${NOFAB}
Per posting: title; dept/team; seniority; location; matched_city (against: ${cityList}); posted_date (if shown); tools_named (systems named in the description — e.g. Unicorn, OSI PI, OpenLab, Genedata, Snowflake, Discoverant, MES, LIMS, Benchling, Python); url; source. THEN set digital_data_it_proxy: based on WHERE digital/data/IT roles concentrate and how they're framed (central "center of excellence" vs site-embedded), a 1-2 sentence observable read, and digital_data_it_confidence. Return up to 20 postings.`,
    { label: 'pilot:jobs', phase: 'Pilot', schema: JOBS }),

  () => agent(
    `Assess, strictly as LABELED HYPOTHESES (never as fact), how digital/data/IT decision power is structured at AstraZeneca for bioprocessing tooling. Use WebSearch + WebFetch for OBSERVABLE proxies only: where digital/data/IT roles are posted, "center of excellence" / shared-services / global-function language, named central orgs (e.g. R&D IT, Operations IT, Data Science & AI), public org signals.
ALSO weigh this internal call-derived context: [STRIPPED FOR THE REPO. The real line names AstraZeneca contacts and their roles from Invert's own calls. Re-add it from HubSpot or the call notes before running.]
Produce: it_centralization_hypothesis + it_centralization_confidence (low/medium/high); digital_data_it_location; decision_power_hypothesis (HQ vs site budget/tooling authority) + decision_power_confidence; evidence (the observable proxies you actually found, each with a source where possible); sources. Everything is inference — do NOT state anything as established fact.`,
    { label: 'pilot:decision', phase: 'Pilot', schema: DECISION }),
]

// ---------- Track B: publications for all 49 accounts ----------
const pubThunks = ACCTS.map(a => () =>
  agent(pubPrompt(a.company, ''), { label: 'pubs:' + a.id, phase: 'Publications', schema: PUBS })
    .then(r => ({ account_id: a.id, company: a.company, ...(r || { publications: [], notes: 'agent returned null' }) })))

const [pilotRes, pubsRes] = await parallel([
  () => parallel(pilotThunks),
  () => parallel(pubThunks),
])

const [lineage, news, pilotPubs, conf, jobs, decision] = pilotRes
return {
  pilot: { lineage, news, publications: pilotPubs, conference: conf, jobs, decision },
  publications_by_account: pubsRes.filter(Boolean),
}

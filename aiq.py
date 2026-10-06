#!/usr/bin/env python3
"""
Shared helpers for the account-intelligence pipeline. stdlib only, python3.9-safe.

Exists because city->site resolution was silently wrong in three ways and every new script needs
the fixed version:

  BUG 1  merge.py's ncity() stripped non-ASCII, so ncity("Sodertalje") with an umlaut produced
         'sdertlje' while the careers-URL slug is 'sodertalje'. 202 of AstraZeneca's 9,844 reqs
         orphaned. Fixed by afold().
  BUG 1b ncity() also stripped parentheticals, but "Molndal (Gothenburg)" carries the name the
         careers site actually uses in the parenthetical. Fixed by indexing parentheticals as
         aliases.
  BUG 2  merge.py's city index used setdefault(), so when two sites of one account normalize to
         the same city the FIRST one silently won every req. AstraZeneca has 'cambridge' twice
         (Kendall Sq MA / Cambridge UK) and 'sodertalje' twice (Gartuna / Snackviken); 77 such
         city-level collisions exist across the active accounts. resolve_site() returns
         AMBIGUOUS + the candidate list instead of guessing.
  BUG 2b Splitting a city string on commas indexes the state/country token as a city, so
         "Cambridge, MA" yields the key 'ma' -- which then matches any ", MA" site across 30
         different accounts. city_keys() keeps the city proper and drops short geo tokens.
  BUG 3  Some sites belong to two active accounts because one owns the other. New Haven CT is
         both astrazeneca--s11 and alexion--s01, and careers.astrazeneca.com serves the Alexion
         reqs. mirror_for() records the subsidiary attribution instead of double counting.

Also: site_id is NOT a durable key. normalize.py reassigns it from a per-account counter driven by
source-CSV row order, so any table that accretes across runs must persist city_slug and re-resolve
site_id every run. See resolve_site() callers.
"""
import contextlib, csv, fcntl, hashlib, json, os, re, sys, time, unicodedata

# Primary web domain per account. Used by Clay people lookups (companyIdentifier) and by the Workday
# tenant discovery, which reads the ATS host off the company careers page. Curated, not inferred:
# guessing 'bristolmyerssquibb.com' from the company name finds nothing (it is bms.com).
DOMAIN = {
 "abbvie":"abbvie.com","agc-biologics":"agcbio.com","alexion":"alexion.com","alnylam":"alnylam.com",
 "amgen":"amgen.com","ascendis-pharma":"ascendispharma.com","astellas":"astellas.com",
 "astrazeneca":"astrazeneca.com","bayer":"bayer.com","beigene":"beigene.com","biogen":"biogen.com",
 "biomarin":"biomarin.com","biontech":"biontech.de","boehringer-ingelheim":"boehringer-ingelheim.com",
 "bristol-myers-squibb":"bms.com","chugai":"chugai-pharm.co.jp","csl":"csl.com",
 "daiichi-sankyo":"daiichisankyo.com","eisai":"eisai.com","eli-lilly":"lilly.com",
 "fujifilm":"fujifilmdiosynth.com","genentech":"gene.com","genmab":"genmab.com",
 "gilead-sciences":"gilead.com","gsk":"gsk.com","incyte":"incyte.com","ipsen":"ipsen.com",
 "jazz-pharmaceuticals":"jazzpharma.com","johnson-johnson":"jnj.com","lonza":"lonza.com",
 "lundbeck":"lundbeck.com","merck-and-co":"merck.com","merck-kgaa":"merckgroup.com",
 "moderna":"modernatx.com","novartis":"novartis.com","novavax":"novavax.com",
 "novo-nordisk":"novonordisk.com","otsuka":"otsuka-us.com","pfizer":"pfizer.com",
 "recipharm":"recipharm.com","regeneron":"regeneron.com","roche":"roche.com",
 "samsung-biologics":"samsungbiologics.com","sanofi":"sanofi.com","seagen":"seagen.com",
 "takeda":"takeda.com","ucb":"ucb.com","vertex":"vrtx.com","zoetis":"zoetis.com",
}

# Email domains that are ALSO this account, beyond DOMAIN: operating units and legacy brands whose
# mailboxes are still live. Anything else (shire.com at Takeda, trilliumtherapeutics.com at Pfizer) is
# treated as a probably-dead address by merge_clay.py.
EMAIL_DOMAINS = {
 "abbvie": ["allergan.com"], "alexion": ["astrazeneca.com"], "amgen": ["horizontherapeutics.com"],
 "astrazeneca": ["alexion.com", "medimmune.com"], "biomarin": ["bmrn.com"],
 "bristol-myers-squibb": ["celgene.com"], "csl": ["cslbehring.com", "seqirus.com", "cslvifor.com"],
 "fujifilm": ["fujifilm.com", "fujifilmdiosynth.com"], "genentech": ["roche.com"],
 "gilead-sciences": ["kitepharma.com"], "johnson-johnson": ["janssen.com", "janssen-cilag.de", "its.jnj.com"],
 "merck-and-co": ["msd.com", "merck.us"], "merck-kgaa": ["emdserono.com", "emdgroup.com", "milliporesigma.com"],
 "otsuka": ["otsuka.co.jp", "otsuka-us.com"], "roche": ["gene.com"], "sanofi": ["genzyme.com"],
 "seagen": ["pfizer.com"], "takeda": ["takeda.com"],
}
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "cache")

# Resolution outcomes, ordered best -> worst. Stored in postings.site_resolution.
EXACT, ALIAS, METRO, AMBIGUOUS, UNMATCHED, NOT_IN_SITES, HUMAN = (
    "exact_city", "alias", "metro", "ambiguous", "unmatched", "city_not_in_sites", "human")

# Extraction tiers, ordered for the monotonic-upgrade guard: a cheap re-extract must never
# overwrite a richer one. tier_legacy has content but unverifiable provenance, so it sits above
# the shell tiers and below anything we fetched ourselves.
TIERS = ["tier0_index", "tier1_shell", "tier_legacy", "tier2_archive_body", "tier3_live_body",
         "tier4_manual"]
def tier_rank(t):
    return TIERS.index(t) if t in TIERS else -1

# Parent account -> subsidiaries whose sites its careers domain also serves. Deliberately an
# explicit list: inferring it from shared cities is wrong, because unrelated companies routinely
# have sites in the same city (bothell has five, cambridge has twenty-five).
SUBSIDIARY = {
    "astrazeneca": ["alexion"],
    "pfizer": ["seagen"],
    "roche": ["genentech"],
}

# ---------------------------------------------------------------- text normalisation

def afold(s):
    """Strip diacritics so Sodertalje-with-umlauts == sodertalje. THE fix for BUG 1."""
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", norm(afold(s))).strip("-")

def ckey(s):
    """Collapse a city name to a comparison key: fold, lowercase, drop non-alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", afold(s or "").lower())

# Tokens that are a region/state/country, not a city. Indexing these creates keys like 'ma' that
# match across dozens of accounts (BUG 2b).
_GEO_STOP = {
    "usa", "us", "uk", "eu", "ireland", "england", "scotland", "wales", "sweden", "denmark",
    "germany", "france", "italy", "spain", "switzerland", "belgium", "netherlands", "austria",
    "japan", "china", "india", "canada", "mexico", "brazil", "singapore", "korea", "poland",
    "portugal", "puertorico", "northireland", "greatbritain",
}

def _admissible(k):
    """A city key must be >2 chars and not a bare state/country token."""
    return len(k) > 2 and k not in _GEO_STOP

def city_keys(raw):
    """
    Split a sites.csv city value into (primary, alias) key sets.

      "Cambridge, MA"          -> ({'cambridge'}, set())          # 'ma' dropped  (BUG 2b)
      "Molndal (Gothenburg)"   -> ({'molndal'}, {'gothenburg'})   # parenthetical kept (BUG 1b)
      "Liverpool (Speke)"      -> ({'liverpool'}, {'speke'})
      "Sodertalje"             -> ({'sodertalje'}, set())         # umlauts folded (BUG 1)
      "Cambridge / Waltham, MA"-> ({'cambridge','waltham'}, set())# both are real cities
    """
    if not raw:
        return set(), set()
    raw = afold(raw)
    inner = re.findall(r"\(([^)]*)\)", raw)
    outer = re.sub(r"\([^)]*\)", " ", raw)
    # The city proper is everything before the first comma; what follows is state/country.
    head = outer.split(",")[0]
    primary = {k for k in (ckey(p) for p in re.split(r"[/&]|\bor\b", head)) if _admissible(k)}
    alias = set()
    for blob in inner:
        for p in re.split(r"[,/&]", blob):
            k = ckey(p)
            if _admissible(k):
                alias.add(k)
    return primary, alias - primary

def metro_keys(raw):
    """Metro is coarser than city and gets its own namespace, so it can never outrank a city."""
    if not raw:
        return set()
    raw = afold(re.sub(r"\([^)]*\)", " ", raw))
    out = set()
    for p in re.split(r"[,/&]", raw):
        k = ckey(p)
        if _admissible(k):
            out.add(k)
    return out

def slug_to_key(url_city_slug):
    """
    careers-URL city segment -> comparison key, matching what city_keys() produces on the site side.

      'mount-vernon'              -> 'mountvernon'
      'new-haven'                 -> 'newhaven'
      'Cambridge-MA'              -> 'cambridge'          # Workday appends the state
      'Research-Triangle-Park-NC' -> 'researchtrianglepark'

    The trailing-token strip matters because Workday hosts encode the state into the path segment,
    so without it every Workday city key ('cambridgema') misses a site index built from
    'Cambridge, MA' (BUG 2b's mirror image on the URL side).
    """
    from urllib.parse import unquote
    raw = afold(unquote(url_city_slug or ""))
    toks = [t for t in re.split(r"[-_\s,]+", raw) if t]
    while toks and (len(toks[-1]) <= 2 or toks[-1].lower() in _GEO_STOP):
        toks.pop()
    return ckey("".join(toks)) if toks else ckey(raw)

# ---------------------------------------------------------------- place aliases
#
# The site spine and the ATS routinely name the same place differently. Biogen's NC site is
# "Durham, NC" in sites.csv while Workday returns "Research Triangle Park, NC" for 39 requisitions --
# so those reqs resolved to nothing and the Durham lane truthfully reported a zero it had been given.
# Same failure family as the Sodertalje/Gothenburg bug, just on the ATS side.
#
# Curated and explicit on purpose. Fuzzy matching here would resurrect BUG 2: "cambridge" must stay
# ambiguous between AstraZeneca's MA and UK sites, and no amount of string similarity can settle that
# -- only a human can, via org_overrides.json -> site_picks.
#
# Each entry maps an ATS spelling to the site-spine key(s) it should also match. Multiple targets are
# allowed and are DELIBERATELY not disambiguated: if a synonym legitimately covers two of an
# account's sites, resolve_site returns AMBIGUOUS, which is the correct answer.
PLACE_ALIASES = {
    "researchtrianglepark": ["durham", "morrisville", "raleigh"],
    "rtp": ["durham", "morrisville", "raleigh"],
    "cambridgema": ["cambridge"],
    "kendallsquare": ["cambridge"],
    "southsanfranciscoca": ["southsanfrancisco"],
    "ssf": ["southsanfrancisco"],
    "thousandoaksca": ["thousandoaks"],
    "greaterboston": ["boston", "cambridge"],
    "bostonma": ["boston"],
    "sanfranciscobayarea": ["sanfrancisco", "southsanfrancisco"],
    "newyorkcity": ["newyork"],
    "nyc": ["newyork"],
    "washingtondc": ["washington"],
    "gaithersburgmd": ["gaithersburg"],
    "kingofprussiapa": ["kingofprussia"],
    "westpoint": ["westpoint"],
    "macclesfielduk": ["macclesfield"],
    "cambridgeuk": ["cambridge"],
    "sodertaljesweden": ["sodertalje"],
    "gothenburgsweden": ["gothenburg", "molndal"],
    "molndalsweden": ["molndal", "gothenburg"],
    "baselswitzerland": ["basel"],
    "dublinireland": ["dublin"],
    "copenhagendenmark": ["copenhagen", "hillerod"],
}

# Location strings that carry no place at all. These are NOT unmatched-city findings; recording them
# as "sites we are missing" would bury the real ones.
NON_PLACES = {"remote", "remoteusa", "remoteus", "fieldbased", "homebased", "various",
              "multiplelocations", "flexible", "virtual", "anywhere", "nationwide", ""}


def alias_targets(key):
    return PLACE_ALIASES.get(key, [])


# ---------------------------------------------------------------- site index

def build_site_index(sites):
    """
    {account_id: {'city': {key: [site_id,...]}, 'alias': {...}, 'metro': {...}}}

    Values are LISTS, not single ids. That is the whole point: a colliding key must surface every
    candidate so the caller can mark it ambiguous rather than silently taking the first (BUG 2).
    """
    idx = defaultdict(lambda: {"city": defaultdict(list), "alias": defaultdict(list),
                               "metro": defaultdict(list)})
    for s in sites:
        aid = s["account_id"]
        pri, ali = city_keys(s.get("city", ""))
        for k in sorted(pri):
            idx[aid]["city"][k].append(s["site_id"])
        for k in sorted(ali):
            idx[aid]["alias"][k].append(s["site_id"])
        for k in sorted(metro_keys(s.get("metro", ""))):
            idx[aid]["metro"][k].append(s["site_id"])
    # Reverse-index the curated synonyms into the ALIAS bucket, so a synonym can never outrank a real
    # city match and a synonym covering two sites still reports AMBIGUOUS.
    for aid, buckets in idx.items():
        for syn, targets in PLACE_ALIASES.items():
            hits = []
            for t in targets:
                hits += buckets["city"].get(t, [])
            if hits and syn not in buckets["city"]:
                for sid in hits:
                    if sid not in buckets["alias"][syn]:
                        buckets["alias"][syn].append(sid)
    return idx

def resolve_site(idx, account_id, city_raw, overrides=None):
    """
    -> (site_id, resolution, candidates)

    site_id is "" whenever the answer is not a single site. Never guesses. Tiers city > alias >
    metro so a metro key can never outrank a real city match.

    overrides: {"<account_id>/<city_key>": "<site_id>"} from org_overrides.json, letting a human
    settle a genuine ambiguity (e.g. astrazeneca/cambridge -> the UK or the MA site) permanently.
    """
    key = ckey(city_raw) if city_raw else ""
    if not key:
        return "", UNMATCHED, []
    if overrides:
        pick = overrides.get("%s/%s" % (account_id, key))
        if pick:
            return pick, HUMAN, [pick]
    a = idx.get(account_id)
    if not a:
        return "", NOT_IN_SITES, []
    for bucket, res in (("city", EXACT), ("alias", ALIAS), ("metro", METRO)):
        hits = a[bucket].get(key)
        if hits:
            uniq = sorted(set(hits))
            if len(uniq) == 1:
                return uniq[0], res, uniq
            return "", AMBIGUOUS, uniq
    return "", NOT_IN_SITES, []

def mirror_for(idx, parent_account_id, city_raw):
    """
    A req found on the parent's careers domain may physically belong to a subsidiary that is its
    own active account. Returns (mirror_account_id, mirror_site_id, basis).

    New Haven CT resolves under BOTH astrazeneca--s11 ("New Haven R&D CoE (Alexion)") and
    alexion--s01; without this, AstraZeneca books 102 New Haven reqs and Alexion shows zero hiring
    at its own PD Center of Excellence (BUG 3). The row keeps account_id = the domain owner and
    gains the mirror, so a brief can show it under both with provenance visible and no row
    duplicated.

    basis is recorded rather than collapsed, because the two cases carry different certainty and
    neither should be asserted silently:
      dual_registered  -- parent AND subsidiary both have an exact/alias site in this city. The
                          same physical site is registered twice. Mirroring is safe.
      subsidiary_only  -- only the subsidiary matches exactly. The req may be the subsidiary's, or
                          it may be at a parent location missing from sites.csv. Flagged, not
                          asserted; the brief renders it as a candidate.
    """
    p_sid, p_res, _ = resolve_site(idx, parent_account_id, city_raw)
    for sub in SUBSIDIARY.get(parent_account_id, []):
        sid, res, _ = resolve_site(idx, sub, city_raw)
        if sid and res in (EXACT, ALIAS):
            basis = "dual_registered" if (p_sid and p_res in (EXACT, ALIAS)) else "subsidiary_only"
            return sub, sid, basis
    return "", "", ""

# ---------------------------------------------------------------- job family / seniority

# Ordered: first match wins, so put the specific patterns before the general ones.
FAMILIES = [
    ("lab_systems", r"\blims\b|\beln\b|benchling|labware|labvantage|empower|chromeleon|unicorn"
                    r"|genedata|discoverant|lab-informatics|sample-management"),
    ("automation_mes", r"automation|\bmes\b|\bscada\b|\bplc\b|\bdcs\b|historian|osi-?pi\b|\bpat\b"
                       r"|process-control|instrumentation|\bgamp\b|control-system"),
    ("data_digital", r"process-data|data-scien|data-engineer|data-analyt|data-architect"
                     r"|data-governance|data-steward|data-platform|data-management|digital"
                     r"|analytic|informatic|bioinformat|statistic|biostatistic|machine-learning"
                     r"|artificial-intelligence|\bai\b|\bml\b|modelling|modeling|simulation"
                     r"|computational|software|operations-it\b"),
    ("msat_mfg_sci", r"\bmsat\b|manufacturing-scien|technical-operation|tech-transfer"
                     r"|manufacturing-technolog|process-engineer"),
    ("cell_gene", r"cell-therapy|gene-therapy|viral-vector|car-?t\b|lentivir|\baav\b|apheresis"),
    ("process_dev", r"process-development|upstream|downstream|purification|cell-culture"
                    r"|fermentation|chromatograph|drug-substance|drug-product|formulation"
                    r"|fill-finish|bioprocess|\bcmc\b|cell-line"),
    ("analytical", r"analytical|bioassay|potency|characteri[sz]ation|\bqc\b|quality-control"
                   r"|method-development"),
]
ORG_CORE = {"data_digital", "automation_mes", "lab_systems"}
BIOPROCESS = {"msat_mfg_sci", "process_dev", "analytical", "cell_gene"}

# HARD excludes are never admitted, whatever the family. An intern or apprentice req describes a
# programme, not an org: it names no team scope and no reporting line, so it burns a body fetch for
# nothing. Keeping these in the same bucket as the soft ones let "automation-engineer-summer-intern"
# through, because automation_mes is ORG_CORE and ORG_CORE was allowed to override the exclude.
HARD_EXCLUDE = (r"intern\b|internship|summer-intern|apprentice|graduate-program|placement-student"
                r"|co-op\b|nurse|physician|\bmsl\b|medical-science-liaison")
# SOFT excludes are commercial/back-office signals that an ORG_CORE family may legitimately
# override -- e.g. a genuine "Director, Commercial Data Platform" is still data_digital.
SOFT_EXCLUDE = (r"sales|account-manager|commercial|market-access|medical-affairs|field-"
                r"|territory|brand|marketing|recruit|talent-acquisition|human-resource|payroll"
                r"|finance|\btax\b|treasur|legal|counsel|procurement|facilit"
                r"|clinical-research-associate|clinical-trial|pharmacovigilance|patient-safety"
                r"|regulatory-affairs")
EXCLUDE = HARD_EXCLUDE + "|" + SOFT_EXCLUDE

SENIORITY = [
    (6, r"^(vp|vice-president|head-of|executive-director|chief)"),
    (5, r"^(senior-director|sr-director|director)"),
    (4, r"^(associate-director|assoc-director|principal)"),
    (3, r"^(senior-manager|manager|lead)"),
    (2, r"^(senior|sr|staff)"),
]

def classify(role_slug):
    """
    -> (family, family_rule, seniority_rank, seniority_rule, excluded)

    First family match wins, so FAMILIES is ordered specific-first. The matched substring is
    returned as family_rule and stored on the row, so a misclassification is one grep away rather
    than a mystery.
    """
    s = "-" + (role_slug or "").lower() + "-"
    fam, rule = "", ""
    for name, pat in FAMILIES:
        m = re.search(pat, s)
        if m:
            fam, rule = name, m.group(0)
            break
    rank, srule = 1, ""
    bare = (role_slug or "").lower()
    for r, pat in SENIORITY:
        m = re.search(pat, bare)
        if m:
            rank, srule = r, m.group(0)
            break
    hard = re.search(HARD_EXCLUDE, s)
    soft = re.search(SOFT_EXCLUDE, s)
    ex = ("hard:" + hard.group(0)) if hard else (("soft:" + soft.group(0)) if soft else "")
    return fam, rule, rank, (srule or "default"), ex

def admit(family, seniority_rank, excluded, icp_fit, role_slug=""):
    """
    Tier-2 admission. ORG_CORE families are always worth a body fetch; bioprocess families only at
    director+ or when the slug already names a tool. ICP fit gates everything, because org
    structure at a site we would never sell into is not worth tokens. A hard exclude always wins.
    """
    if icp_fit not in ("yes", "soft"):
        return False
    if (excluded or "").startswith("hard:"):
        return False
    names_tool = bool(re.search(r"unicorn|osi-?pi|genedata|discoverant|benchling|empower|\bmes\b"
                                r"|\blims\b|\beln\b|snowflake|databricks|historian", role_slug or ""))
    ok = family in ORG_CORE or (family in BIOPROCESS and (seniority_rank >= 3 or names_tool))
    if excluded.startswith("soft:") and family not in ORG_CORE:
        ok = False
    return ok

def score(family, seniority_rank, icp_fit, recent, repost_count):
    return (3 * seniority_rank
            + (4 if family in ORG_CORE else 1)
            + (3 if icp_fit == "yes" else 1)
            + (2 if recent else 0)
            + min(max(repost_count - 1, 0), 3))

# ---------------------------------------------------------------- csv / json io

def load(name, base=None):
    p = os.path.join(base or DATA, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))

# A writer must never silently shrink an accreting table. This fired for real: a long-running
# fetch_bodies process held a pre-Biogen snapshot in memory and its checkpoint write deleted all 203
# Biogen rows -- twice, the second time because the already-running process still had the old code
# loaded after the file was patched. Killing the process fixes the cause; this guard makes the
# symptom loud instead of silent.
ACCRETING = {"postings.csv", "posting_quotes.csv", "org_units.csv", "org_edges.csv"}
SHRINK_TOLERANCE = 0.02      # 2% -- dedup/pruning noise, not row loss

def write(name, rows, fields, base=None, allow_shrink=False):
    if name in ACCRETING and not allow_shrink and not os.environ.get("AIQ_ALLOW_SHRINK"):
        prev = load(name, base)
        if prev and len(rows) < len(prev) * (1 - SHRINK_TOLERANCE):
            raise SystemExit(
                "REFUSING TO WRITE %s: %d rows -> %d rows (%.0f%% loss). An accreting table should "
                "never shrink like this; the usual cause is a stale in-memory snapshot from a "
                "concurrent or long-running writer. Check for other running scripts, then re-run. "
                "Set AIQ_ALLOW_SHRINK=1 only if the loss is genuinely intended."
                % (name, len(prev), len(rows), 100.0 * (1 - len(rows) / float(len(prev)))))
    p = os.path.join(base or DATA, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return p

LOCK = os.path.join(DATA, ".write.lock")

@contextlib.contextmanager
def lock(timeout=120):
    """
    Advisory write lock for the SSOT csvs.

    Every writer here does read-modify-write on a whole file, so two writers running at once means
    the slower one saves a stale snapshot and silently deletes the other's rows. That is not
    theoretical: running fetch_bodies.py in the background while jobs_workday.py ingested Biogen
    wiped all 203 Biogen rows, because the body-fetcher still held a pre-Biogen copy in memory and
    checkpointed it back. Hold this lock across the whole read-modify-write, not just the write.
    """
    os.makedirs(DATA, exist_ok=True)
    f = open(LOCK, "a+")
    start = time.time()
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (IOError, OSError):
            if time.time() - start > timeout:
                f.close()
                raise SystemExit("could not acquire the SSOT write lock in %ds -- another writer is "
                                 "still running. Wait for it, or remove %s if it is stale."
                                 % (timeout, LOCK))
            time.sleep(0.4)
    try:
        yield
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def patch(name, fields, updates, key="posting_id"):
    """
    Re-read a table under the lock, apply per-row field updates, write it back. Use this instead of
    writing a whole in-memory snapshot when other processes may also be writing.
    updates: {key_value: {field: value}}
    """
    with lock():
        rows = load(name)
        idx = {r[key]: r for r in rows}
        added = 0
        for k, ch in updates.items():
            r = idx.get(k)
            if r is None:
                continue
            r.update(ch)
        write(name, rows, fields)
        return len(rows), added


UNMATCHED_FIELDS = ["account_id", "city_raw", "key", "n", "n_admitted", "first_seen", "last_seen",
                    "verdict"]


def record_unmatched(postings):
    """
    Derive data/unmatched_cities.csv from the corpus: every location string that resolved to no site,
    ranked by requisition count.

    This is the honest half of the alias fix. PLACE_ALIASES only ever covers what somebody noticed;
    this table is where the ones nobody noticed become visible. It also separates two things that
    look identical in `city_not_in_sites`:
      needs_alias   -- we have a site in that area under another name (a mapping gap, cheap to fix)
      missing_site  -- a real location absent from the 604-site spine. Biogen's "Baar, Switzerland"
                       (17 reqs) is its Swiss commercial HQ; Luterbach is the manufacturing site.
                       That is a finding for the site-mapping work, not a bug in this pipeline.
      no_place      -- "Remote, USA" and friends. Not a finding at all.
    """
    agg = {}
    for r in postings:
        if r.get("site_id") or r.get("site_resolution") in (AMBIGUOUS,):
            continue
        raw = (r.get("city_raw") or "").strip()
        key = ckey(raw.split(",")[0]) if raw else ""
        k = (r["account_id"], raw)
        a = agg.setdefault(k, {"account_id": r["account_id"], "city_raw": raw, "key": key,
                               "n": 0, "n_admitted": 0, "first_seen": "", "last_seen": ""})
        a["n"] += 1
        if r.get("tier2_admit") == "true":
            a["n_admitted"] += 1
        for fld, cmp_ in (("first_seen", min), ("last_seen", max)):
            v = r.get(fld) or ""
            a[fld] = cmp_(a[fld], v) if a[fld] and v else (v or a[fld])
    rows = []
    for a in agg.values():
        if a["key"] in NON_PLACES:
            a["verdict"] = "no_place"
        elif alias_targets(a["key"]):
            a["verdict"] = "needs_alias"
        else:
            a["verdict"] = "missing_site"
        a["n"], a["n_admitted"] = str(a["n"]), str(a["n_admitted"])
        rows.append(a)
    rows.sort(key=lambda r: (r["account_id"], -int(r["n"])))
    write("unmatched_cities.csv", rows, UNMATCHED_FIELDS)
    return rows


OVERRIDES = os.path.join(HERE, "org_overrides.json")

def load_overrides():
    """
    Human judgement layer. Seed-don't-clobber, mirroring build-site.py's load_board(): fill in
    missing keys, never overwrite what a person wrote.
    """
    o = {}
    if os.path.exists(OVERRIDES):
        try:
            o = json.load(open(OVERRIDES, encoding="utf-8"))
        except ValueError as e:
            sys.exit("org_overrides.json is not valid JSON (%s) -- refusing to run and silently "
                     "discard human edits." % e)
    o.setdefault("_readme", "Hand-editable. Never regenerated. site_picks settles genuine city "
                            "ambiguities; unit/edge verdicts and pins come from the briefs' export "
                            "button. Re-run build_org.py after editing.")
    o.setdefault("site_picks", {})      # "astrazeneca/cambridge": "astrazeneca--s18"
    o.setdefault("unit_verdicts", {})   # unit_id -> {"v":"confirm|reject","note":...}
    o.setdefault("edge_verdicts", {})
    o.setdefault("unit_renames", {})
    o.setdefault("pins", {})            # account_id -> [{ref,rank,why}]
    o.setdefault("queue", [])
    return o

def save_overrides(o):
    json.dump(o, open(OVERRIDES, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

# ---------------------------------------------------------------- ids / run ledger

def h10(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]

def posting_id(account_id, req_id, city_slug, role_slug):
    """
    Content-derived, deliberately NOT a counter. The CDX result set legitimately changes between
    runs (Wayback backfills captures, new reqs appear); a counter would renumber and every human
    annotation would reattach to the wrong posting.
    """
    if req_id:
        return "%s--jr-%s" % (account_id, req_id)
    return "%s--jr-h%s" % (account_id, h10(city_slug, role_slug))

def unit_id(account_id, unit_slug):
    return "%s--ou-%s" % (account_id, unit_slug)

def edge_id(account_id, edge_type, src, dst):
    return "%s--oe-%s-%s" % (account_id, edge_type, h10(src, dst))

def new_run_id(kind):
    return "%s-%s" % (kind, time.strftime("%Y%m%dT%H%M%S"))

RUN_FIELDS = ["run_id", "kind", "started_at", "finished_at", "accounts", "urls_seen", "new_urls",
              "extracted", "quarantined", "verify_pass_rate", "notes"]

def log_run(rec):
    rows = load("jobs_runs.csv")
    rows.append(rec)
    write("jobs_runs.csv", rows, RUN_FIELDS)

def today():
    return time.strftime("%Y-%m-%d")

def quarter(yyyymmdd):
    """'20240612' or '2024-06-12' -> '2024Q2'"""
    d = re.sub(r"[^0-9]", "", yyyymmdd or "")
    if len(d) < 6:
        return ""
    return "%sQ%d" % (d[:4], (int(d[4:6]) - 1) // 3 + 1)

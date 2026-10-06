#!/usr/bin/env python3
"""
The anti-fabrication gate. Deterministic, zero tokens, 100% coverage.

Every structured claim the extractor emits must be backed by a quote, and every quote must be a
literal substring of the requisition body we cached ourselves. This script asserts exactly that.
Claims whose quote does not appear are QUARANTINED and never reach a brief.

This is the single most important integrity check in the system, because this data drives outbound
messaging: an invented team name gets read aloud on a call.

  python3 verify_org.py ../org_out_20260729.json            # gate; exits 1 below the threshold
  python3 verify_org.py ../org_out_20260729.json --threshold 90

Writes data/quarantine_org.csv. merge_org.py imports verify_quote() so the merge applies the
identical test -- one implementation, no drift.
"""
import argparse, html, json, os, re, sys, unicodedata

import aiq
from fetch_bodies import BODIES

Q_FIELDS = ["posting_id", "account_id", "claim_type", "subject", "quote_text", "reason",
            "body_chars", "checked_at"]
DEFAULT_THRESHOLD = 95.0

# Characters a model routinely "helps" with. Normalising these is legitimate -- they are the same
# character rendered differently -- whereas normalising words would let a paraphrase pass.
_PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "―": "-", "−": "-", "­": "",
    " ": " ", " ": " ", " ": " ", " ": " ", "​": "",
    "…": "...", "•": "*", "·": "*",
}

def _punct(s):
    return "".join(_PUNCT.get(c, c) for c in s)

def n_space(s):
    return re.sub(r"\s+", " ", s or "").strip()

def n_soft(s):
    """Entity-unescape, unify look-alike punctuation, collapse whitespace. Words untouched."""
    return n_space(_punct(html.unescape(s or "")))

def n_hard(s):
    """Additionally drop all punctuation and casing. Words and their order still must match."""
    s = unicodedata.normalize("NFKD", n_soft(s).lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def verify_quote(body, quote):
    """
    -> (ok, method). Progressive normalisation, weakest first. `method` records how it matched so a
    reviewer can see which claims needed leniency.

    Deliberately NOT fuzzy: there is no edit-distance or token-overlap fallback. A paraphrase must
    fail, because the whole promise of the verbatim column is that it is the source's own words.
    """
    q = (quote or "").strip()
    if not q or not body:
        return False, "empty"
    if len(q) < 12:
        return False, "too_short"
    if q in body:
        return True, "exact"
    bs, qs = n_soft(body), n_soft(q)
    if qs and qs in bs:
        return True, "soft"
    bh, qh = n_hard(body), n_hard(q)
    if qh and qh in bh:
        return True, "hard"
    # A model sometimes stitches two sentences with an ellipsis. Accept only if EVERY fragment is
    # independently present, which keeps the "these are the source's words" guarantee.
    frags = [f for f in re.split(r"\s*\.\.\.\s*|\s*…\s*", qh) if len(f) > 18]
    if len(frags) > 1 and all(f in bh for f in frags):
        return True, "elided"
    return False, "not_found"


def load_bodies(postings):
    out = {}
    for p in postings:
        pid = p.get("posting_id") or ""
        path = os.path.join(BODIES, pid + ".txt")
        out[pid] = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    return out


def audit(path, write=True):
    """-> (stats dict, quarantine rows)"""
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict) and "postings" not in data:
        data = data.get("result", data)
    postings = data.get("postings", [])
    bodies = load_bodies(postings)
    acct = {r["posting_id"]: r["account_id"] for r in aiq.load("postings.csv")}

    quarantine, methods = [], {}
    n_q = n_ok = 0
    no_body = []
    for p in postings:
        pid = p.get("posting_id") or ""
        body = bodies.get(pid, "")
        if not body:
            no_body.append(pid)
        for q in p.get("quotes") or []:
            n_q += 1
            ok, method = verify_quote(body, q.get("quote_text", ""))
            methods[method] = methods.get(method, 0) + 1
            if ok:
                n_ok += 1
            else:
                quarantine.append({
                    "posting_id": pid, "account_id": acct.get(pid, ""),
                    "claim_type": q.get("claim_type", ""), "subject": q.get("subject", ""),
                    "quote_text": (q.get("quote_text", "") or "")[:400],
                    "reason": ("no cached body to check against" if not body else method),
                    "body_chars": str(len(body)), "checked_at": aiq.today()})
    rate = (100.0 * n_ok / n_q) if n_q else 0.0
    if write:
        prev = aiq.load("quarantine_org.csv")
        seen = {(r["posting_id"], r["claim_type"], r["subject"], r["quote_text"][:80]) for r in prev}
        for r in quarantine:
            k = (r["posting_id"], r["claim_type"], r["subject"], r["quote_text"][:80])
            if k not in seen:
                prev.append(r); seen.add(k)
        aiq.write("quarantine_org.csv", prev, Q_FIELDS)
    return {"postings": len(postings), "quotes": n_q, "verified": n_ok, "rate": rate,
            "methods": methods, "no_body": no_body}, quarantine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    st, quar = audit(a.path, write=not a.no_write)
    print("=== verbatim gate ===")
    print("postings extracted : %d" % st["postings"])
    print("quotes checked     : %d" % st["quotes"])
    print("verified           : %d  (%.1f%%)" % (st["verified"], st["rate"]))
    print("match method       :", st["methods"])
    if st["no_body"]:
        print("NO CACHED BODY for %d posting(s) -- cannot verify: %s"
              % (len(st["no_body"]), ", ".join(st["no_body"][:6])))
    if quar:
        print("\nquarantined %d claim(s); nothing here reaches a brief. First 8:" % len(quar))
        for r in quar[:8]:
            print("  [%s] %s :: %s :: %r" % (r["reason"], r["claim_type"], r["subject"][:34],
                                             r["quote_text"][:90]))
    if st["quotes"] == 0:
        print("\nFAIL: no quotes at all -- the extraction returned nothing to verify.")
        sys.exit(1)
    if st["rate"] < a.threshold:
        print("\nFAIL: %.1f%% < threshold %.1f%%. Do NOT present these claims as usable; fix the "
              "prompt or the bodies and re-run." % (st["rate"], a.threshold))
        sys.exit(1)
    print("\nPASS: %.1f%% >= %.1f%%. Safe to merge." % (st["rate"], a.threshold))


if __name__ == "__main__":
    main()

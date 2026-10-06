#!/usr/bin/env python3
"""Merge one source-check run into data/jev/<prefix>_final.csv (read by jev_site_final.py).

Inputs for a run <prefix>: data/jev/<prefix>_verification.csv (jev_verify_sources.py) and
data/jev/<prefix>_adjudication.json ({decisions, checks} from the adjudicate workflow).

  adjudicated site: the decision stands only if the skeptic agreed, else NEEDS_PERSON
  source-confirmed site not adjudicated: the policy-map verdict stands (the source supports it
  and Jev either agreed or was unsure), with the source as its link

  python3 jev_check_merge.py check206
"""
import csv, json, os, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
JEV = os.path.join(HERE, "data", "jev")


def same(a, b):
    return a == b or {a, b} <= {"CLOSED", "GAP", "DROP"}


def main(prefix):
    V = {r["site_id"]: r for r in csv.DictReader(open(os.path.join(JEV, prefix + "_verification.csv"), encoding="utf-8"))}
    A = json.load(open(os.path.join(JEV, prefix + "_adjudication.json"), encoding="utf-8"))
    D = {d["site_id"]: d for d in A["decisions"]}
    K = {c["site_id"]: c for c in A["checks"]}
    F = {r["site_id"]: r for r in csv.DictReader(open(os.path.join(JEV, "site_fit.csv"), encoding="utf-8"))}
    S = {r["site_id"]: r for r in csv.DictReader(open(os.path.join(HERE, "data", "sites.csv"), encoding="utf-8"))}
    out = []
    for sid, v in V.items():
        f, d = F[sid], D.get(sid)
        base = {"site_id": sid, "account_id": f["account_id"], "site": S[sid]["site"], "city": S[sid]["city"],
                "claude_map": f["claude_verdict"], "policy_map": f.get("policy_verdict", ""), "jev_verdict": f["jev_verdict"],
                "source_kind": v.get("best_kind", ""), "source_date": v.get("best_date", "")}
        if d:
            ok = K.get(sid, {}).get("agree", False)
            final = d["final_verdict"] if ok and d["final_verdict"] != "NEEDS_PERSON" else "NEEDS_PERSON"
            how = "person needed" if final == "NEEDS_PERSON" else (
                "confirmed on review" if same(base["policy_map"], final) else "corrected")
            base.update({"final_verdict": final, "how": how, "reason": d["reason_tag"],
                         "source_url": d.get("deciding_url") or v.get("best_url", ""), "source_quote": d.get("deciding_quote", "")})
        elif v["outcome"] == "confirmed":
            base.update({"final_verdict": base["policy_map"] or f["jev_verdict"], "how": "confirmed by source",
                         "reason": f.get("reason_tag", ""), "source_url": v.get("best_url", ""), "source_quote": ""})
        else:
            base.update({"final_verdict": "NEEDS_PERSON", "how": "person needed", "reason": v["outcome"],
                         "source_url": v.get("best_url", ""), "source_quote": ""})
        fv = base["final_verdict"]
        base["map_right"] = "" if fv == "NEEDS_PERSON" else str(same(base["policy_map"], fv))
        base["jev_right"] = "" if fv == "NEEDS_PERSON" or base["jev_verdict"] == "REVIEW" else str(same(base["jev_verdict"], fv))
        out.append(base)
    cols = ["site_id", "account_id", "site", "city", "claude_map", "policy_map", "jev_verdict", "final_verdict", "how",
            "reason", "source_url", "source_quote", "source_kind", "source_date", "map_right", "jev_right"]
    with open(os.path.join(JEV, prefix + "_final.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    dec = [r for r in out if r["final_verdict"] != "NEEDS_PERSON"]
    jd = [r for r in dec if r["jev_right"]]
    print(prefix, "sites", len(out), "| how", dict(Counter(r["how"] for r in out)))
    print("map right", sum(r["map_right"] == "True" for r in dec), "/", len(dec),
          "| Jev right", sum(r["jev_right"] == "True" for r in jd), "/", len(jd),
          "| with source", sum(1 for r in out if r["source_url"]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "check206")

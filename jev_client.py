#!/usr/bin/env python3
"""Minimal TypeSafe (Jev) client for python 3.9 -- urllib only, no SDK.

The official typesafe-sdk needs python >= 3.10; this Mac has 3.9, so this speaks
the HTTP API directly: POST https://api.typesafe.ai/v1/systemone.

Key: read from $TYPESAFE_API_KEY, else from the macOS Keychain item
TYPESAFE_API_KEY. It is never written to disk, logged, or put in a request
anywhere except the Authorization header.

Every response is cached under data/cache/jev/<hash>.json (same request -> no
second call) and its token usage is appended to data/cache/jev/usage.jsonl.
The model is pinned so thresholds tuned in a pilot stay meaningful.
"""
import hashlib, json, os, subprocess, time, urllib.error, urllib.request

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "cache", "jev")
RETRY = {429, 500, 502, 503, 504, 529}


def _key():
    k = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not k:
        try:
            k = subprocess.run(["security", "find-generic-password", "-s", "TYPESAFE_API_KEY", "-w"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            k = ""
    if not k:
        raise SystemExit("TypeSafe key not found: set TYPESAFE_API_KEY or add Keychain item TYPESAFE_API_KEY")
    return k


def ask(state, questions, model=MODEL, retries=4, timeout=30, tag=""):
    """One request. questions = {id: {type, instructions, criteria?}}. Returns the parsed response."""
    body = json.dumps({"state": state, "model": model, "questions": questions},
                      sort_keys=True, ensure_ascii=False).encode("utf-8")
    h = hashlib.sha256(body).hexdigest()[:24]
    path = os.path.join(CACHE, h + ".json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
    t0 = time.time()
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code in RETRY and attempt < retries:
                wait = e.headers.get("retry-after")
                time.sleep(min(30.0, float(wait) if wait else 2.0 ** attempt))
                continue
            detail = e.read()[:400].decode("utf-8", "replace")
            raise RuntimeError(f"TypeSafe HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries:
                time.sleep(2.0 ** attempt)
                continue
            raise
    os.makedirs(CACHE, exist_ok=True)
    json.dump(resp, open(path, "w", encoding="utf-8"))
    with open(os.path.join(CACHE, "usage.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": round(t0), "hash": h, "tag": tag, "model": resp.get("model"),
                            "n_questions": len(questions), "state_chars": len(json.dumps(state)),
                            "secs": round(time.time() - t0, 3), "usage": resp.get("usage")}) + "\n")
    return resp


def usage_summary():
    p = os.path.join(CACHE, "usage.jsonl")
    if not os.path.exists(p):
        return "no Jev calls logged"
    rows = [json.loads(l) for l in open(p, encoding="utf-8")]
    tin = sum((r.get("usage") or {}).get("input_tokens", 0) for r in rows)
    return f"{len(rows)} calls, {tin:,} input tokens, ${tin * 0.042 / 1e6:.4f} at $0.042/1M"


if __name__ == "__main__":
    print(usage_summary())

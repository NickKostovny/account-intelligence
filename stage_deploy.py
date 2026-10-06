#!/usr/bin/env python3
"""
Stage briefs/ into deploy/ for the gated Vercel project, and nothing else.

The deploy is deliberately manual and two-step, because this content is confidential (real target
accounts, named individuals, deal status). This script only prepares the folder:

  python3 stage_deploy.py            # copy briefs/*.html + jobs-*.json -> deploy/, write vercel.json,
                                     # point deploy/.vercel at invert/account-intel
  python3 stage_deploy.py --check <url>   # unauthenticated fetch of <url>; PASS only if the response
                                     # is the Vercel SSO redirect and the FULL body carries none of
                                     # the confidential markers below

Then, from deploy/ with the sandbox off -- PREVIEW deploy, then move the team alias by hand:
  /opt/homebrew/bin/vercel deploy --yes --scope invert                     # prints the hash URL
  /opt/homebrew/bin/vercel alias set <hash-url> account-intel-invert.vercel.app --scope invert
  python3 stage_deploy.py --check-all                                     # every URL form, expected state
NEVER `--prod` here. A production deploy re-creates Vercel's default production alias, which for this
project is `account-intel-two.vercel.app` (assigned because account-intel.vercel.app was taken), and
that default alias is NOT behind Vercel Authentication under this plan: on 2026-09-10 it served the
briefs at HTTP 200 for ~40 minutes before the check caught it. It was removed (404) and --check-all
now asserts it stays 404. The bare account-intel.vercel.app belongs to an unrelated third party.

Project: invert/account-intel (team_Xku7jES7Ne4OzSr6WyhwGHcC / prj_qSXVDLc9RvRp1oWtM5r8y5KuyU8C),
created empty on 2026-09-10, canary-verified: hash URL and -invert alias both 302 -> vercel.com/sso-api.
The older invert-account-intel project in the personal nick-8710s-projects scope is abandoned: the
harness cannot read or set protection there.
"""
import argparse, glob, json, os, re, shutil, sys, urllib.parse, urllib.request, urllib.error
import aiq

DEPLOY = os.path.join(aiq.HERE, "deploy")
BRIEFS = os.path.join(aiq.HERE, "briefs")
LINK = {"projectId": "prj_qSXVDLc9RvRp1oWtM5r8y5KuyU8C", "orgId": "team_Xku7jES7Ne4OzSr6WyhwGHcC",
        "projectName": "account-intel"}
VERCEL_JSON = {
    "cleanUrls": False,
    "headers": [{"source": "/(.*)", "headers": [
        {"key": "X-Robots-Tag", "value": "noindex, nofollow, noarchive"},
        {"key": "Referrer-Policy", "value": "no-referrer"},
        {"key": "Cache-Control", "value": "private, no-store"}]}],
}
# Strings that appear in every real brief and in no login page. If any shows up unauthenticated,
# the gate is open.
MARKERS = ["account intelligence", "named site head", "AstraZeneca", "Site Head", "KEEP"]


def stage():
    os.makedirs(os.path.join(DEPLOY, ".vercel"), exist_ok=True)
    for f in glob.glob(os.path.join(DEPLOY, "*.html")) + glob.glob(os.path.join(DEPLOY, "*.json")):
        os.remove(f)
    n = 0
    for f in glob.glob(os.path.join(BRIEFS, "*.html")) + glob.glob(os.path.join(BRIEFS, "jobs-*.json")):
        shutil.copy2(f, DEPLOY)
        n += 1
    json.dump(VERCEL_JSON, open(os.path.join(DEPLOY, "vercel.json"), "w"), indent=2)
    pj = os.path.join(DEPLOY, ".vercel", "project.json")
    if os.path.exists(pj):
        cur = json.load(open(pj))
        if cur != LINK and not os.path.exists(pj + ".prev"):
            shutil.copy2(pj, pj + ".prev")
    json.dump(LINK, open(pj, "w"))
    print("staged %d files -> deploy/ ; linked to %s/%s" % (n, "invert", LINK["projectName"]))


def check(url):
    req = urllib.request.Request(url, headers={"User-Agent": "gate-check"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:   # follows redirects
            body = r.read().decode("utf-8", "replace")
            final, code = r.geturl(), r.status
    except urllib.error.HTTPError as e:
        body, final, code = e.read().decode("utf-8", "replace"), e.geturl(), e.code
    # The SSO login page echoes the requested URL in its `next=` parameter, so a marker that is also
    # part of the path (".../astrazeneca.html") would false-positive. Scan the body without that echo.
    scan = re.sub(r"next=[^\"'&\s]*", "", body, flags=re.I)
    scan = scan.replace(url, "").replace(urllib.parse.quote(url, safe=""), "")
    hits = [m for m in MARKERS if m.lower() in scan.lower()]
    sso = "vercel.com/sso-api" in final or "vercel.com/login" in final or "Login" in body[:5000]
    print("url:     ", url)
    print("final:   ", final)
    print("status:  ", code, "| body bytes:", len(body))
    print("markers: ", hits or "none")
    ok = sso and not hits
    print("GATE:", "PASS" if ok else "FAIL -- do not share this URL")
    return ok


def status_of(url):
    req = urllib.request.Request(url, headers={"User-Agent": "gate-check"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def check_all():
    """
    Expected state of every URL form:
      account-intel-invert.vercel.app      -> SSO wall, no markers   (the link people use)
      account-intel-two.vercel.app         -> 404                    (Vercel's default alias; must stay removed)
      account-intel.vercel.app             -> third party, not ours  (reported, never shared)
    """
    ok = check("https://account-intel-invert.vercel.app/")
    two = status_of("https://account-intel-two.vercel.app/")
    print("account-intel-two.vercel.app -> HTTP %d %s" % (two, "(ok, absent)" if two == 404 else "<<< PUBLIC DEFAULT ALIAS IS BACK: run `vercel alias rm account-intel-two.vercel.app --yes --scope invert`"))
    ok = ok and two == 404
    print("account-intel.vercel.app -> third party's site, not ours; never share it")
    print("ALL:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check")
    ap.add_argument("--check-all", action="store_true",
                    help="assert the expected state of every URL form of the project")
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if check(a.check) else 1)
    if a.check_all:
        sys.exit(0 if check_all() else 1)
    stage()

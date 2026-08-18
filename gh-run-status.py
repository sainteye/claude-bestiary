#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Poll one repository's GitHub Actions status in the background and write a cache file. The
status line only ever reads that file.

    python3 ~/.claude/gh-run-status.py <repo directory> <cache file>

**The cache format is generic; GitHub Actions is only the first producer.** Any deployment
system — Cloud Build, Vercel, a script of your own — that writes this shape gets drawn:

    {"state": "running|ok|fail|none",     # none = nothing worth showing
     "label": "deploy",                    # the word next to the spinner
     "started_at": 1786430000,             # epoch, for the elapsed time
     "updated_at": 1786430120,
     "steps": [{"name": "verify", "state": "ok"},
               {"name": "build",  "state": "running"}],
     "sha": "d47a60c", "title": "...", "url": "https://...",
     "head_in_run": false}                 # has the HEAD I am holding made it into this run

It looks at the branch's most recent workflow run rather than at a pull request: a project that
pushes to main and deploys from Actions has no PR checks at all.
"""
import json
import os
import subprocess
import sys
import time

TIMEOUT = 20


def gh_bin():
    for cand in (os.environ.get("GH_PATH"), "/opt/homebrew/bin/gh",
                 "/usr/local/bin/gh", "/usr/bin/gh"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    from shutil import which
    return which("gh")


def run(args, cwd, timeout=TIMEOUT):
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


STATE_OF = {"success": "ok", "failure": "fail", "timed_out": "fail",
            "startup_failure": "fail", "cancelled": "cancel", "skipped": "skip",
            "action_required": "fail", "neutral": "ok"}


def classify(status, conclusion):
    if status in ("queued", "in_progress", "waiting", "pending", "requested"):
        return "running"
    return STATE_OF.get(conclusion, "other")


def write(path, payload):
    payload["updated_at"] = int(time.time())
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


#: How long a local deploy's own status stays authoritative. Two numbers because they answer
#: different questions: **while it runs** we must not let a stale CI verdict overwrite what is
#: happening right now; **after it ends** the verdict is worth reading for a while, and then it
#: is history. The running one is deliberately generous — a slow deploy is still a deploy — but
#: it is not infinite, because a script killed mid-flight would otherwise spin forever.
LOCAL_RUNNING_TTL = 1800
LOCAL_DONE_TTL = 900


def local_deploy_holds(out):
    """Is a local deploy (`make gcp-deploy`) currently the truth about deployment?

    Both producers write the same file, so one of them has to give way. The local one wins
    while it is fresh, for a plain reason: **it is the one actually shipping.** When CI is
    disabled — which is exactly when the local path gets used — the newest GitHub run is a
    stale failure that has nothing to do with what is on the wire right now.
    """
    try:
        with open(out, encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:
        return False
    if cur.get("producer") != "local":
        return False
    age = time.time() - (cur.get("updated_at") or 0)
    return age < (LOCAL_RUNNING_TTL if cur.get("state") == "running" else LOCAL_DONE_TTL)


#: How long a *failed* CI run keeps showing. A tick already expires after 15 minutes in the
#: status line ("a permanent tick is decoration nobody reads") while a cross used to stay
#: forever — and a light that is always red is indistinguishable from a broken light. Failures
#: earn far longer than successes, but not eternity. Whether production is actually healthy is
#: a different cell's job (the health light), and that one is live.
FAIL_TTL = 6 * 3600


def main():
    repo, out = sys.argv[1], sys.argv[2]
    if local_deploy_holds(out):
        return
    gh = gh_bin()
    if not gh:
        write(out, {"state": "none", "why": "no-gh"})
        return

    branch = (run(["git", "branch", "--show-current"], repo, 3) or "").strip()
    if not branch:
        write(out, {"state": "none", "why": "no-branch"})
        return

    # Fetch several recent runs at once, to work out how long this workflow usually takes —
    # which is what the progress bar is made of. The bar matters more than the spinner: the
    # status line redraws 0.5 times a second on average (measured), so a spinner never turns
    # smoothly, while "2m14s of a usual 6m" reads correctly however slow the redraws are.
    raw = run([gh, "run", "list", "--branch", branch, "--limit", "15",
               "--json", "databaseId,status,conclusion,headSha,displayTitle,"
                         "createdAt,startedAt,updatedAt,url,workflowName"], repo)
    if not raw:
        write(out, {"state": "none", "why": "gh-failed"})
        return
    try:
        runs = json.loads(raw)
    except Exception:
        runs = []
    if not runs:
        write(out, {"state": "none", "why": "no-runs"})
        return

    r = runs[0]
    state = classify(r.get("status"), r.get("conclusion"))

    def epoch(ts):
        try:
            t = int(time.mktime(time.strptime((ts or "")[:19], "%Y-%m-%dT%H:%M:%S")))
            return t - (time.altzone if time.localtime().tm_isdst else time.timezone)
        except Exception:
            return None

    # Completed runs of the same workflow; the median is "how long this usually takes"
    durs = []
    for x in runs:
        if x.get("status") != "completed" or x.get("workflowName") != r.get("workflowName"):
            continue
        a, b = epoch(x.get("startedAt") or x.get("createdAt")), epoch(x.get("updatedAt"))
        if a and b and 10 < b - a < 7200:
            durs.append(b - a)
    durs.sort()
    typical = durs[len(durs) // 2] if durs else None
    # A disabled workflow's last failure is not news — it is a headstone. Nothing will ever
    # replace it, so without this it stays red forever, and a light that is always red is
    # indistinguishable from a broken one. (This is the state you are in right after switching
    # deploys to a local path because CI cannot run.)
    if state == "fail":
        wf_raw = run([gh, "workflow", "list", "--all", "--json", "name,state"], repo, 8)
        try:
            disabled = {w.get("name") for w in json.loads(wf_raw or "[]")
                        if w.get("state") != "active"}
        except Exception:
            disabled = set()
        if r.get("workflowName") in disabled:
            write(out, {"state": "none", "why": "workflow-disabled"})
            return

    started = epoch(r.get("startedAt") or r.get("createdAt"))
    if state == "fail" and started and time.time() - started > FAIL_TTL:
        # Old news. Keep `why` so the next person reading this file can tell "nothing to show"
        # apart from "the poller is broken" — the same distinction the empty-list rule makes.
        write(out, {"state": "none", "why": "stale-fail"})
        return

    payload = {
        "state": state,
        "label": (r.get("workflowName") or "ci").lower().split()[0],
        "started_at": started,
        "typical_seconds": typical,
        "sha": (r.get("headSha") or "")[:7],
        "title": (r.get("displayTitle") or "")[:60],
        "url": r.get("url"),
    }

    # Has the HEAD I am holding made it into that run? This answers "which version is actually
    # deployed" without going and looking, which is the first question when something is stuck.
    full = r.get("headSha") or ""
    if full:
        try:
            rc = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", full],
                                cwd=repo, capture_output=True, timeout=3).returncode
            payload["head_in_run"] = (rc == 0)
        except Exception:
            pass

    # Only fetch the individual jobs mid-run; that API call buys nothing once it has finished
    if state == "running" and r.get("databaseId"):
        jraw = run([gh, "run", "view", str(r["databaseId"]), "--json", "jobs"], repo)
        try:
            jobs = json.loads(jraw or "{}").get("jobs") or []
        except Exception:
            jobs = []
        payload["steps"] = [
            {"name": (j.get("name") or "")[:14],
             "state": classify(j.get("status"), j.get("conclusion"))}
            for j in jobs
        ][:4]

    write(out, payload)


if __name__ == "__main__":
    main()

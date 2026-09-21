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
REMOTE_CHECK_TIMEOUT = 8


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


def resolve_branch(repo):
    """Return (remote branch, remote, source), using local git data only.

    An upstream is the exact mapping the current branch is configured to push to. Without one,
    a cached remote HEAD is a better account of the repository's CI branch than the arbitrary
    local name. The local name is still useful as the last fallback for feature branches.
    """
    local = (run(["git", "branch", "--show-current"], repo, 3) or "").strip()
    if not local:
        return None, None, None

    remotes = (run(["git", "remote"], repo, 3) or "").splitlines()
    remotes = [remote.strip() for remote in remotes if remote.strip()]
    if not remotes:
        return local, None, "local"

    upstream = (run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name",
                     "@{upstream}"], repo, 3) or "").strip()
    for remote in sorted(remotes, key=len, reverse=True):
        prefix = remote + "/"
        if upstream.startswith(prefix):
            return upstream[len(prefix):], remote, "upstream"

    configured = (run(["git", "config", "--get", "branch.%s.remote" % local], repo, 3)
                  or "").strip()
    if configured not in remotes:
        configured = (run(["git", "config", "--get", "remote.pushDefault"], repo, 3)
                      or "").strip()
    if configured not in remotes:
        configured = "origin" if "origin" in remotes else sorted(remotes)[0]

    head = (run(["git", "symbolic-ref", "--quiet", "--short",
                 "refs/remotes/%s/HEAD" % configured], repo, 3) or "").strip()
    prefix = configured + "/"
    if head.startswith(prefix) and len(head) > len(prefix):
        return head[len(prefix):], configured, "remote-default"
    return local, configured, "local"


def remote_has_branch(repo, remote, branch):
    """True/False when the remote answers; None when it cannot be reached.

    This deliberately runs only after GitHub returned an empty run list. Checking every refresh
    would make the common path slower; not checking at all would turn "wrong branch" into
    "no runs" again.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--heads", remote,
             "refs/heads/%s" % branch],
            cwd=repo, capture_output=True, text=True, timeout=REMOTE_CHECK_TIMEOUT)
    except Exception:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
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
    branch, remote, branch_source = resolve_branch(repo)
    if not branch:
        write(out, {"state": "none", "why": "no-branch"})
        return
    if not remote:
        write(out, {"state": "none", "why": "remote-unavailable", "detail": "no-remote"})
        return

    gh = gh_bin()
    if not gh:
        write(out, {"state": "none", "why": "remote-unavailable", "detail": "no-gh"})
        return

    # Fetch several recent runs at once, to work out how long this workflow usually takes —
    # which is what the progress bar is made of. The bar matters more than the spinner: the
    # status line redraws 0.5 times a second on average (measured), so a spinner never turns
    # smoothly, while "2m14s of a usual 6m" reads correctly however slow the redraws are.
    raw = run([gh, "run", "list", "--branch", branch, "--limit", "15",
               "--json", "databaseId,status,conclusion,headSha,displayTitle,"
                         "createdAt,startedAt,updatedAt,url,workflowName"], repo)
    if raw is None:
        write(out, {"state": "none", "why": "remote-unavailable", "detail": "gh-failed"})
        return
    try:
        runs = json.loads(raw)
    except Exception:
        write(out, {"state": "none", "why": "remote-unavailable",
                    "detail": "invalid-gh-response"})
        return
    if not runs:
        exists = remote_has_branch(repo, remote, branch)
        detail = {"branch": branch, "branch_source": branch_source, "remote": remote}
        if exists is False:
            write(out, dict(detail, state="none", why="branch-missing"))
        elif exists is True:
            write(out, dict(detail, state="none", why="no-runs"))
        else:
            write(out, dict(detail, state="none", why="remote-unavailable",
                            detail="branch-check-failed"))
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

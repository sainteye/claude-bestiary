#!/usr/bin/env python3
"""Exercise gh-run-status.py against real temporary git repositories.

The GitHub CLI is replaced with a tiny fixture, but branch/upstream/default-remote
resolution and the final branch-existence check all run through git itself. Nothing
under ~/.claude is read or written.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
POLLER = ROOT / "gh-run-status.py"
GIT = shutil.which("git")
FAILURES = []


def command(*args, cwd=None, env=None, check=True):
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=check)


def git(repo, *args):
    return command(GIT, *args, cwd=repo).stdout.strip()


def make_repo(tmp, remote=True):
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "commit", "--allow-empty", "-qm", "fixture")
    git(repo, "branch", "-M", "main")
    if remote:
        bare = tmp / "remote.git"
        command(GIT, "init", "--bare", "-q", str(bare))
        git(repo, "remote", "add", "origin", str(bare))
        git(repo, "push", "-qu", "origin", "main")
    return repo


def make_fake_gh(tmp):
    fake = tmp / "gh"
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\\n")
if args[:2] == ["run", "list"]:
    if os.environ.get("FAKE_GH_FAIL"):
        sys.exit(1)
    branch = args[args.index("--branch") + 1]
    if branch != os.environ.get("FAKE_GH_RUN_BRANCH"):
        print("[]")
    else:
        print(json.dumps([{
            "databaseId": 7, "status": "completed", "conclusion": "success",
            "headSha": os.environ["FAKE_GH_HEAD"], "displayTitle": "fixture",
            "createdAt": "2026-09-21T00:00:00Z",
            "startedAt": "2026-09-21T00:00:00Z",
            "updatedAt": "2026-09-21T00:01:00Z", "url": "https://example.invalid/run/7",
            "workflowName": "CI"
        }]))
elif args[:2] == ["workflow", "list"]:
    print("[]")
else:
    print("{}")
""", encoding="utf-8")
    fake.chmod(0o755)
    return fake


def poll(repo, tmp, run_branch=None, gh_fails=False):
    fake = make_fake_gh(tmp)
    out = tmp / "status.json"
    log = tmp / "gh.log"
    env = dict(os.environ, GH_PATH=str(fake), FAKE_GH_LOG=str(log),
               FAKE_GH_HEAD=git(repo, "rev-parse", "HEAD"))
    if run_branch is not None:
        env["FAKE_GH_RUN_BRANCH"] = run_branch
    if gh_fails:
        env["FAKE_GH_FAIL"] = "1"
    result = command(sys.executable, str(POLLER), str(repo), str(out), env=env, check=False)
    if result.returncode != 0:
        raise AssertionError("poller exited %d: %s" % (result.returncode, result.stderr))
    payload = json.loads(out.read_text(encoding="utf-8"))
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] \
        if log.exists() else []
    return payload, calls


def check(name, body):
    try:
        with tempfile.TemporaryDirectory(prefix="gh-run-status-") as raw:
            body(Path(raw))
        print("✓ " + name)
    except Exception as exc:
        FAILURES.append((name, exc))
        print("✗ %s: %s" % (name, exc))


def assert_why(payload, why, detail=None):
    assert payload.get("state") == "none", payload
    assert payload.get("why") == why, payload
    if detail is not None:
        assert payload.get("detail") == detail, payload


def upstream_beats_local(tmp):
    repo = make_repo(tmp)
    git(repo, "branch", "-m", "local-name")
    payload, calls = poll(repo, tmp, run_branch="main")
    assert payload["state"] == "ok", payload
    run_call = next(call for call in calls if call[:2] == ["run", "list"])
    assert run_call[run_call.index("--branch") + 1] == "main", run_call


def remote_default_beats_local(tmp):
    repo = make_repo(tmp)
    git(repo, "branch", "--unset-upstream")
    git(repo, "branch", "-m", "local-name")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    payload, _ = poll(repo, tmp, run_branch="main")
    assert payload["state"] == "ok", payload


def local_name_is_last_fallback(tmp):
    repo = make_repo(tmp)
    git(repo, "branch", "--unset-upstream")
    payload, _ = poll(repo, tmp, run_branch="main")
    assert payload["state"] == "ok", payload


def missing_branch_is_not_no_runs(tmp):
    repo = make_repo(tmp)
    git(repo, "branch", "--unset-upstream")
    git(repo, "branch", "-m", "not-on-remote")
    payload, _ = poll(repo, tmp)
    assert_why(payload, "branch-missing")


def existing_branch_without_runs(tmp):
    repo = make_repo(tmp)
    payload, _ = poll(repo, tmp)
    assert_why(payload, "no-runs")


def no_remote_is_unavailable(tmp):
    repo = make_repo(tmp, remote=False)
    payload, calls = poll(repo, tmp)
    assert_why(payload, "remote-unavailable", "no-remote")
    assert not calls, calls


def gh_failure_is_unavailable(tmp):
    repo = make_repo(tmp)
    payload, _ = poll(repo, tmp, gh_fails=True)
    assert_why(payload, "remote-unavailable", "gh-failed")


def branch_check_failure_is_unavailable(tmp):
    repo = make_repo(tmp)
    git(repo, "remote", "set-url", "origin", str(tmp / "gone.git"))
    payload, _ = poll(repo, tmp)
    assert_why(payload, "remote-unavailable", "branch-check-failed")


for name, body in (
    ("the upstream branch is queried instead of the differently named local branch", upstream_beats_local),
    ("the cached remote default branch is the second choice", remote_default_beats_local),
    ("the local branch name remains the last fallback", local_name_is_last_fallback),
    ("a branch absent from the remote is not reported as no-runs", missing_branch_is_not_no_runs),
    ("an existing remote branch with no runs is reported as no-runs", existing_branch_without_runs),
    ("a repository with no remote is reported as unavailable", no_remote_is_unavailable),
    ("a gh authentication/network failure is reported as unavailable", gh_failure_is_unavailable),
    ("a failed remote branch check is reported as unavailable", branch_check_failure_is_unavailable),
):
    check(name, body)

if FAILURES:
    sys.exit(1)
print("all good")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read one repository's backlog summary in the background and write a cache file. The status
line only ever reads that file.

    python3 ~/.claude/backlog-status.py <repo directory> <cache file>

**The grouping is not computed here.** A `lane` (now / scheduled / waiting / drop) is derived
from "how badly does this hurt, and how long would it take", and that derivation exists once,
in the repository being asked. This only asks it, and stores the answer.

Computing it a second time somewhere else ends the same way every time: the two implementations
eventually disagree, and **nobody notices that drift**, because the number on the status line
goes on looking correct.

## The shape of the cache file (generic; the first producer to write one was a web app)

    {"total": 53,
     "lanes": {"now": 2, "scheduled": 10, "waiting": 19, "drop": 22},
     "stale": 0,            # sitting there with a trigger that has never come true
     "guesses": 7,          # justified by "guessing: ..."
     "artifact": "/abs/path/....html",    # the page a Cmd-click opens
     "source": "/abs/path/backlog.yaml",  # the data file — how the status line knows to recount
     "source_mtime": 1786855529.0,        # when that file was last changed, as of this count
     "ok": true}

**`source` plus `source_mtime` decides when to recount. Time does not.** This number reads one
local file, so there is no reason to poll on a TTL — which is what made a change you had just
saved take up to ten minutes to appear (measured 2026-08-16: thirteen items out of date). The
repository reports its own `source`, because "which file the backlog lives in" should not have a
second answer.

Any project whose command prints this shape gets drawn. **`ok: false` exists on purpose**: "this
repository has no backlog" and "it has one and reading it failed" are not the same thing, and a
blank draws them identically — the same lesson a `root_exists` flag taught elsewhere.
"""
import json
import os
import subprocess
import sys
import time

TIMEOUT = 20

#: Where to ask. **A list of (python, script) relative paths rather than one hard-coded
#: command**: the next project's venv will not necessarily live under backend/, and adding a
#: row is cheaper than changing logic.
# Where a repository keeps the thing that can count its backlog.
#
# `""` for the interpreter means "whatever python is running this", which is what a repository
# with no virtualenv needs — and a tool that made a venv a condition of being counted would only
# ever find projects shaped like the first one.
#
# **This list is the only thing standing between a repository and being invisible here.** Adding a
# convention is cheap; a project that has a backlog and is never asked for it looks identical to a
# project that has none.
PROBES = [
    ("backend/.venv/bin/python", "backend/scripts/build_backlog_artifact.py"),
    ("", "tools/backlog.py"),
]


def probe(repo):
    for py, script in PROBES:
        py_path = os.path.join(repo, py) if py else sys.executable
        script_path = os.path.join(repo, script)
        if not (os.path.isfile(py_path) and os.path.isfile(script_path)):
            continue
        try:
            out = subprocess.run(
                [py_path, script_path, "--json"],
                cwd=os.path.join(repo, os.path.dirname(script)) or repo,
                capture_output=True, text=True, timeout=TIMEOUT,
            )
        except Exception:
            return {"ok": False, "detail": "the probe would not run"}
        if out.returncode != 0:
            return {"ok": False, "detail": (out.stderr or "").strip()[-120:]}
        try:
            data = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:
            return {"ok": False, "detail": "the output is not JSON"}
        data["ok"] = True
        # Record when the source was last modified as of this count. The status line compares
        # that against the file's mtime now, which answers "does this still hold" without any
        # timer being involved.
        src = data.get("source")
        if src:
            try:
                data["source_mtime"] = os.path.getmtime(src)
            except OSError:
                pass
        return data
    return None    # this repository has no backlog — not broken, just not applicable


def main():
    if len(sys.argv) < 3:
        return 2
    repo, path = sys.argv[1], sys.argv[2]
    data = probe(repo)
    if data is None:
        # Write "not applicable" so the status line stops calling this every few minutes
        data = {"ok": True, "total": 0, "lanes": {}, "artifact": ""}
    data["updated_at"] = int(time.time())
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)     # atomic: the status line never reads a half-written JSON
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

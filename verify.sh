#!/bin/bash
# Check that every file under ~/.claude **really is a symlink back into this repository**, and
# that the status line still runs.
#
# This exists for one specific failure: a Claude Code update, or one hand-edit, can replace a
# symlink with a real file. From that moment the version in the repository is no longer the
# version running — **and neither side reports an error, your changes simply stop taking
# effect.** (The same illness in another form: a bind-mounted config whose inode a deploy
# replaces, after which the container goes on reading the file that was deleted.)
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude"
FAIL=0

for rel in statusline.py statusline-command.sh project-icon.py gh-run-status.py \
           health-check.py usage-report.py backlog-status.py \
           skills/project-icon/SKILL.md; do
    dst="$DEST/$rel"
    if [ ! -L "$dst" ]; then
        if [ -e "$dst" ]; then
            echo "✗ $rel is a real file, not a symlink — the repository's version is not running"
        else
            echo "✗ $rel is missing (run ./install.sh)"
        fi
        FAIL=1
    elif [ "$(readlink "$dst")" != "$REPO/$rel" ]; then
        echo "✗ $rel points somewhere else: $(readlink "$dst")"
        FAIL=1
    else
        echo "✓ $rel"
    fi
done

# The registry is deliberately not in the list above: it is yours, it is not a symlink into
# this repository, and it may well be a symlink into a private one of your own. All that matters
# is that something is there for the status line to read.
REG="$DEST/project-icons.json"
if [ -e "$REG" ]; then
    echo "✓ project-icons.json ($( [ -L "$REG" ] && echo "→ $(readlink "$REG")" || echo "your own file"))"
else
    echo "✗ project-icons.json is missing (run ./install.sh)"
    FAIL=1
fi

# Nothing transcript-shaped belongs in here. A structural second line, not the only one.
if git -C "$REPO" ls-files 2>/dev/null | grep -qE '\.jsonl$|^projects/|history'; then
    echo "✗ a transcript-shaped file has appeared in the repository — look at this now"
    FAIL=1
fi

# Actually run it, rather than only checking that the files exist.
OUT=$(printf '%s' '{"workspace":{"current_dir":"'"$HOME"'","project_dir":"'"$HOME"'"},"model":{"display_name":"test"},"context_window":{"used_percentage":1}}' \
      | bash "$DEST/statusline-command.sh" 2>/dev/null)
# The phrase comes from statusline-command.sh's failure branch; the two have to agree.
if [ -z "$OUT" ] || printf '%s' "$OUT" | grep -q "status line crashed"; then
    echo "✗ the status line does not run"
    FAIL=1
else
    echo "✓ the status line runs ($(printf '%s' "$OUT" | grep -c '') lines)"
fi

# ── the run cell's own rules ─────────────────────────────────────────────
# One rule of the `run-*.json` format lives in the **reader** rather than in a poller: a
# `running` row that nobody retracted has to stop being drawn (`RUN_STALE_AFTER` in
# `run_segment()`, or `stale_after` in the file). There is no test suite in this repository, and
# an untested rule in a file nobody opens at three in the morning is an unwritten one — so it
# is checked here, against fixtures in a scratch directory. Never against the real cache.
#
# This one reads the *repository's* statusline.py. The symlink checks above are what say $DEST
# is the same file; this is a check of the code, not of the installation.
RUN_TMP=$(mktemp -d)
if python3 - "$REPO" "$RUN_TMP" <<'PY'
# ── the run cell's own rules ───────────────────────────────────────────────
# There is no test suite in this repository, and one rule in the `run-*.json` format lives in
# the **reader** rather than in a poller: a `running` row nobody retracted has to stop being
# drawn. An untested rule in a file nobody opens at three in the morning is an unwritten one,
# so it is checked here, against fixtures in a scratch directory — never the real cache.
#
# This one reads the repository's copy rather than $DEST's. The symlink checks above are what
# say the two are the same file; this is a check of the code, not of the installation.
import importlib.util
import json
import os
import sys
import time

repo, tmp = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("sl", os.path.join(repo, "statusline.py"))
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)
sl.CACHE_DIR = tmp                       # not ~/.claude/statusline-cache. Never that.


class Frozen(object):
    """The `time` module with one hand held still.

    `stale_after` is a boundary — *older than*, not *at least* — and a boundary measured
    against a clock that moves between writing the fixture and reading it is a check that
    passes on a fast morning and fails on a slow one. Everything else is delegated.
    """

    def __init__(self, real, at):
        self._real, self.at = real, at

    def __getattr__(self, name):
        return getattr(self._real, name)

    def time(self):
        return self.at


NOW = int(time.time())
sl.time = Frozen(time, float(NOW))

PROJ = "/Users/nobody/code/run-cell-check"
bad = []


def check(name, ok):
    if not ok:
        bad.append(name)


def draw(**payload):
    """Write one fixture and return what the cell draws for it."""
    with open(os.path.join(tmp, "run-%s.json" % sl.path_key(PROJ)), "w") as f:
        json.dump(payload, f)
    return sl.run_segment(PROJ)


# The name is the project directory, every character that is not a letter, digit, `-` or `_`
# replaced — the same key the backlog and health files already use.
check("the key is the path", sl.path_key("/Users/x/code/clawdline") == "-Users-x-code-clawdline")
check("no file draws nothing", sl.run_segment("/Users/nobody/code/absent") is None)

running = dict(state="running", label="test", started_at=NOW - 120,
               typical_seconds=288, updated_at=NOW)
out = draw(**running)
check("running draws the label", out is not None and "test" in out)
check("running draws the bar", out is not None and "▰" in out and "▱" in out)
check("running draws elapsed against typical", out is not None and "2m00s/4m48s" in out)

out = draw(phase="compiling", **running)
check("phase takes the readout's place", out is not None and "compiling" in out
      and "2m00s/4m48s" not in out)

# The rule that is the whole reason this is not `ghrun-`: nothing else retracts a `kill -9`.
check("a running row past stale_after draws nothing",
      draw(state="running", label="test", started_at=NOW - 1000, updated_at=NOW - 901) is None)
check("the boundary itself is still drawn",
      draw(state="running", label="test", started_at=NOW - 1000, updated_at=NOW - 900)
      is not None)
check("stale_after is the producer's to shorten",
      draw(state="running", label="test", updated_at=NOW - 100, stale_after=60) is None)
check("stale_after is the producer's to lengthen",
      draw(state="running", label="test", updated_at=NOW - 100, stale_after=120) is not None)
check("a running row with no updated_at is stale, not fresh",
      draw(state="running", label="test", started_at=NOW) is None)

check("ok draws a tick", (draw(state="ok", label="test", updated_at=NOW) or "").find("✓") >= 0)
check("an old tick expires", draw(state="ok", label="test", updated_at=NOW - 901) is None)
check("fail draws a cross",
      (draw(state="fail", label="test", updated_at=NOW) or "").find("✗") >= 0)
check("fail does not expire",
      draw(state="fail", label="test", updated_at=NOW - 90000) is not None)

# Never a cross for a word this reader has not heard of, and never a crash for a missing key.
check("none draws nothing", draw(state="none", why="no-run", updated_at=NOW) is None)
check("an unknown state draws nothing", draw(state="hydrating", updated_at=NOW) is None)
check("no state at all draws nothing", draw(label="test", updated_at=NOW) is None)
check("one missing key does not lose the row",
      draw(state="running", updated_at=NOW) is not None)
check("a label is not required", "run" in (draw(state="running", updated_at=NOW) or ""))

# Producer text is drawn verbatim — but the status line is exactly two lines.
out = draw(state="running", label="te\nst", phase="comp\rling", updated_at=NOW)
check("producer text cannot add a line", out is not None and "\n" not in out and "\r" not in out)

with open(os.path.join(tmp, "run-%s.json" % sl.path_key(PROJ)), "w") as f:
    f.write("{not json")
check("a half-written file draws nothing", sl.run_segment(PROJ) is None)

for name in bad:
    sys.stderr.write("    ✗ %s\n" % name)
sys.exit(1 if bad else 0)
PY
then
    echo "✓ the run cell draws what docs/producers.md says it draws"
else
    echo "✗ the run cell does not behave as documented — the lines above name the rule"
    FAIL=1
fi
rm -rf "$RUN_TMP"

[ "$FAIL" = 0 ] && echo && echo "all good" || { echo; echo "problems above"; exit 1; }

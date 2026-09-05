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
# `run_segment()`, or `stale_after` in the file) — and a finished verdict, which is not running,
# is not subject to it. There is no test suite in this repository, and an untested rule in a file
# nobody opens at three in the morning is an unwritten one — so it is checked here, against
# fixtures in a scratch directory. Never against the real cache.
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
sl.NO_COLOR = False                      # whatever this terminal is; the link checks need it off


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


def produced(prefix, proj):
    """The filename a producer writes, spelled out rather than asked for.

    Every fixture below is named this way **on purpose**: a check that names the file by calling
    the function it is checking agrees with the reader no matter what the reader decides, which
    is exactly the failure this whole correction is about. `sed 's|/|-|g'` is the producer, and
    this line is that `sed` — for `run-`, and since 2026-09-05 for `health-` and `backlog-` too.
    """
    return "%s-%s.json" % (prefix, proj.replace("/", "-"))


def write(prefix, proj, payload):
    """Put one fixture in the scratch directory under the name a producer would give it."""
    with open(os.path.join(tmp, produced(prefix, proj)), "w") as f:
        json.dump(payload, f)


def draw(proj=None, **payload):
    """Write one run fixture the way `test.sh` would, and return what the cell draws for it."""
    proj = proj or PROJ
    write("run", proj, payload)
    return sl.run_segment(proj)


# The name is the whole project directory with every `/` turned into `-`, and **nothing else**:
# not the characters, and above all not the length. One rule, and it names all three of this
# repository's per-tree files. The key is an interface Clawdline reads too, and a reader does not
# get to hold an opinion about a name it did not choose.
check("the key is the path, `/` and nothing else",
      sl.path_key("/Users/x/code/clawdline") == "-Users-x-code-clawdline")
check("a space survives, because the producer's `sed` leaves it alone",
      sl.path_key("/Users/x/Application Support/y") == "-Users-x-Application Support-y")
check("no file draws nothing", sl.run_segment("/Users/nobody/code/absent") is None)


def truncating(path):
    """The rule `health-` and `backlog-` were named by until 2026-09-05, kept here so the two
    checks below stay a demonstration rather than a description of one."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in path)[-48:]


# `[-48:]` is lossy, and both halves of that are checked rather than described: a long key is
# not shortened, and two trees differing only outside their last 48 characters keep separate
# files. `run-` was the first file named this way and these were its checks; `health-` and
# `backlog-` now share the rule, so they share the checks — one loop over the three, rather
# than the same paragraph written out three times.
LONG = "/Users/sainteye/Library/Application Support/Clawdline/worktrees/bestiary/" + "a" * 20
TAIL = "/worktrees/d710b7de-f565-41e4-a8b8-12177537893a/repo"      # 52 characters, so the part
TWIN_A = "/Users/sainteye/code/alpha" + TAIL                        # that differs falls outside
TWIN_B = "/Users/sainteye/code/bravo" + TAIL                        # the last 48 of either

check("a key longer than 48 characters is not shortened", len(sl.path_key(LONG)) > 48)
check("the twins would collide under the truncating rule",
      truncating(TWIN_A) == truncating(TWIN_B))
check("the twins do not collide under this one", sl.path_key(TWIN_A) != sl.path_key(TWIN_B))

# One registry entry, so `health_segment()` gets past its "not configured" return.
REGISTRY = {"health": {"url": "https://example.invalid/health",
                       "site": "https://example.invalid/", "label": "prod"}}

# Each fixture carries a number that reaches the screen, so "the cell drew something" and "the
# cell drew *this tree's* file" stay different questions — a collision passes the first.
CELLS = (
    ("run", lambda n: dict(state="running", label="run%d" % n, updated_at=NOW),
     lambda proj: sl.run_segment(proj)),
    ("health", lambda n: dict(state="ok", label="h%d" % n, checked_at=NOW),
     lambda proj: sl.health_segment(REGISTRY, proj)),
    # `source_mtime` far in the future so `_backlog_stale()` says the count still holds: a
    # fixture must never spawn the real producer at the real cache.
    ("backlog", lambda n: dict(ok=True, total=n, lanes={"now": 2},
                               source=os.path.join(repo, "statusline.py"),
                               source_mtime=NOW + 10 ** 6),
     lambda proj: sl.backlog_segment(proj)),
)
MARKS = ((PROJ, 101), (LONG, 202), (TWIN_A, 303))

for prefix, fixture, cell in CELLS:
    for proj, n in MARKS:
        write(prefix, proj, fixture(n))
    check("%s- still draws for a short path" % prefix, "101" in (cell(PROJ) or ""))
    check("%s- finds a long path's file" % prefix, "202" in (cell(LONG) or ""))
    check("%s- does not draw one twin's file under the other's name" % prefix,
          "303" not in (cell(TWIN_B) or ""))

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
check("a running row with no updated_at draws nothing",
      draw(state="running", label="test", started_at=NOW) is None)

# ── the six behaviours the first contract did not settle ──────────────────────────────────
# `updated_at` was written as REQUIRED and, two lines later, as one of the fields that are all
# optional but `state`. Two readers resolved that sensibly in opposite directions and each wrote
# a guard around its own answer, so the disagreement ended up defended from both sides. The
# settlement is three sentences: **`updated_at` is required, every other field is optional, and a
# malformed value is an absent one.** These are the checks that hold each half of it down.
#
# `0` is not a missing field. A producer that writes it meant it, and "falsy therefore default"
# is this language's accident rather than a decision anybody made about the format.
check("stale_after 0 expires immediately, rather than meaning 900",
      draw(state="running", label="test", updated_at=NOW - 60, stale_after=0) is None)
check("a negative stale_after expires immediately too",
      draw(state="running", label="test", updated_at=NOW, stale_after=-1) is None)
# Malformed is absent — and what absent costs you differs only because one of the two fields has
# a documented default to fall back on and the other has none.
check("a string updated_at is not coerced; the row is malformed and draws nothing",
      draw(state="running", label="test", updated_at=str(NOW)) is None)
check("a true updated_at is not a number either",
      draw(state="running", label="test", updated_at=True) is None)
check("a string stale_after falls back to the 900 default rather than expiring at 60",
      draw(state="running", label="test", updated_at=NOW - 100, stale_after="60") is not None)
# A clock that went backwards leaves `updated_at` in the future. Draw, rather than hide: the file
# is newer than this reader's idea of now, which is the opposite of stale.
check("a file from the future is drawn, not hidden",
      draw(state="running", label="test", updated_at=NOW + 3600) is not None)

check("ok draws a tick", (draw(state="ok", label="test", updated_at=NOW) or "").find("✓") >= 0)
check("fail draws a cross",
      (draw(state="fail", label="test", updated_at=NOW) or "").find("✗") >= 0)
# **A verdict does not decay, and it is not measured against anything** — so `stale_after` does
# not reach it and neither does the field `stale_after` would be measured from. `ok` had a
# 900-second window here until 2026-09-05; it lost to one expiry rule in this format rather than
# two. (The deploy cell does expire an `ok` after 900 seconds, and that is not the same case: it
# is holding a seat until its poller arrives with GitHub's opinion. Nothing polls this file.)
check("an old tick is still drawn", draw(state="ok", label="test", updated_at=NOW - 901)
      is not None)
check("a very old tick is still drawn",
      draw(state="ok", label="test", updated_at=NOW - 90000) is not None)
check("a tick with no updated_at is still drawn", draw(state="ok", label="test") is not None)
check("fail does not expire",
      draw(state="fail", label="test", updated_at=NOW - 90000) is not None)
check("a cross with no updated_at is still drawn", draw(state="fail", label="test") is not None)

# `log` makes the whole cell Cmd-clickable. `holder` and `tree` are for the person who `cat`s the
# file, the way `title` is in `ghrun-`, and must stay off a line that is already short of width.
out = draw(state="running", label="test", updated_at=NOW, log="/tmp/my-tests.log",
           holder="clawdline-8d", tree="/Users/nobody/code/run-cell-check")
check("log becomes a link", out is not None and "file:///tmp/my-tests.log" in out)
check("holder and tree are not drawn",
      out is not None and "clawdline-8d" not in out and "code-run-cell-check" not in out)
check("a relative log path is not a link",
      "file://" not in (draw(state="running", label="test", updated_at=NOW, log="x.log") or ""))

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

with open(os.path.join(tmp, produced("run", PROJ)), "w") as f:
    f.write("{not json")
check("a half-written file draws nothing", sl.run_segment(PROJ) is None)

for name in bad:
    sys.stderr.write("    ✗ %s\n" % name)
sys.exit(1 if bad else 0)
PY
then
    echo "✓ the run cell draws, and all three per-tree files are named, as docs/producers.md says"
else
    echo "✗ a cell does not behave as documented — the lines above name the rule"
    FAIL=1
fi
rm -rf "$RUN_TMP"

[ "$FAIL" = 0 ] && echo && echo "all good" || { echo; echo "problems above"; exit 1; }

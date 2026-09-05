# Writing the files the status line reads

The status line never touches the network. It reads small JSON files under
`~/.claude/statusline-cache/` and, when one of them is stale, spawns a detached process to
refresh it — which is how a redraw stays around 55ms. This page is about the other side of that
arrangement: **your** deploy script writing those files, so that what you actually ship is what
the line reports.

**Which repository documents which half.** The file *shapes* — every field both readers consume,
one worked example per file — are in Clawdline's
[docs/project-status.md](https://github.com/sainteye/clawdline/blob/main/docs/project-status.md),
and there is no reason to say them twice. This page is what the terminal status line adds on top:
the fields only it draws, the rules that decide whether your file survives the next poll, and how
fast a write is noticed. Read that page for the shapes; read this one before you write a deploy
script, because the rule that decides whether your file survives is not in the shape.

It exists because somebody wrote a deploy script from the shapes alone. It worked for about five
seconds and then the deploy disappeared off the line with nothing to look at. Everything below is
the reason, and it is checked against the code — the constant or the function is named wherever
this page states a number or a rule.

---

## Say `producer: "local"`, or your file is overwritten within seconds

`ghrun-*.json` has **two producers**. Yours, and `gh-run-status.py`, which the status line spawns
itself to ask GitHub Actions what the branch's newest run is doing. They write the same path, so
one of them has to give way, and the whole of that decision is `local_deploy_holds()` in
`gh-run-status.py`:

```python
if cur.get("producer") != "local":
    return False
age = time.time() - (cur.get("updated_at") or 0)
return age < (LOCAL_RUNNING_TTL if cur.get("state") == "running" else LOCAL_DONE_TTL)
```

It runs as the first thing in `main()`. When it holds, the poller exits without writing a byte;
when it does not, the poller writes whatever GitHub says — and on a repository with no runs on
this branch that is `{"state": "none", "why": "no-runs"}`, which **draws nothing at all**. A
`running` file that vanishes into an empty cell, a few seconds after a deploy started, is that
line and nothing more exotic.

The local producer wins for a plain reason, quoted from the function it lives in: *it is the one
actually shipping.* When CI is disabled — which is exactly when a local deploy path gets written
— the newest GitHub run is a stale failure that has nothing to do with what is on the wire right
now.

Three things about that check are worth being exact about, because each of them has the same
symptom:

- **`"local"` is a literal, not a name.** The test is `!= "local"`. `"producer": "deploy.sh"`,
  `"producer": "make"`, `"producer": "cloud-build"` all fail it and all get overwritten.
  Whatever your script is called, the value of this field is `local`.
- **`updated_at` is required, in epoch seconds.** It is what the age is measured from. Leave it
  out and `or 0` makes your file 56 years old, so the hold never holds. Nothing fills it in for
  you: `gh-run-status.py` stamps it in its own `write()`, and that code does not run for your
  file.
- **The age comes from inside the file, not from its mtime.** The status line touches the file's
  mtime before spawning a poller (`os.utime(path, None)`, so a dozen windows do not each start
  one), so the mtime is not evidence that anybody wrote anything. `updated_at` is the only record
  of when you wrote.

Nothing in this repository ever writes `producer`. It is read and never written, which means your
first write always takes the file over from the poller immediately — the traffic only goes the
other way, and only when the hold has expired.

## How long the hold lasts, and why there are two numbers

```python
LOCAL_RUNNING_TTL = 1800      # 30 minutes
LOCAL_DONE_TTL = 900          # 15 minutes
```

`running` gets the generous one and every other state gets the short one, because they answer
different questions. While it runs, a stale CI verdict must not overwrite what is happening right
now, and a slow deploy is still a deploy. After it ends, the verdict is worth reading for a
while, and then it is history.

Two consequences for a producer:

- **You do not need a heartbeat.** Write on state change and nothing else. The elapsed time and
  the progress bar are computed on every redraw from `started_at` and `typical_seconds`, so a
  file written once keeps moving on screen without you. The exception is a deploy that can run
  past thirty minutes: rewrite the same `running` payload with a fresh `updated_at` before that,
  or the poller takes the file while you are still shipping.
- **Your verdict is yours for fifteen minutes.** After that the poller may replace your `ok` or
  `fail` with GitHub's opinion of the branch. That is rarely a loss: a `✓` has already expired by
  then anyway (below), and a project with real CI is better described by CI once the deploy is
  history.

## Who clears a `running` that never finished

Two layers, and a producer needs to know both, because it is natural to assume the reader does
nothing and to therefore build something elaborate.

**Your trap, first.** It is the only layer that works for every reader — Clawdline has no poller;
it draws the file it finds, so a `running` file nobody retracts spins in that bar indefinitely.
`trap 'say fail; exit 1' ERR INT TERM` costs one line and covers a failed command, a Ctrl-C and a
`kill`. The `exit` is not decoration: without it, a `TERM` handler returns into the script, which
carries on and finishes by declaring success. That was measured while writing the example below.

**The reader's TTL, second.** `kill -9`, a laptop that sleeps, a power cut — nothing traps those.
So the ceiling on a stuck spinner is `LOCAL_RUNNING_TTL`: thirty minutes after your last write,
`local_deploy_holds()` stops holding, and since the status line respawns the poller every five
seconds while the file says `running`, the replacement lands on the next redraw. That layer only
exists here, and only when Claude Code reports a repository for the window — which is what makes
your trap the one that has to be right.

That asymmetry is the whole argument for the seventh file's one new rule: `run-*.json` has no
poller at all, so its ceiling is in the reader instead, where every reader of the format gets it.
The `run-*.json` section below is that rule.

## How quickly a write is noticed

In `deploy_segment()`:

```python
running = bool(data and data.get("state") == "running")
# Watch closely mid-run; do not pester GitHub when nothing is happening
if age > (5 if running else 90):
```

Five seconds while a run is in flight, ninety when nothing is happening. `age` there is the cache
file's mtime — the only place in any of this where the mtime, rather than a timestamp inside the
file, decides anything. With `refreshInterval` set in `settings.json` the line redraws on a timer
as well as on events, so a file you write is on screen within a couple of seconds and the
five-second poller looks at it about as often.

That cadence is also the answer to "why did it disappear so fast". Omitting `producer` does not
lose a race by a hair — the poller is invited to overwrite your file **every five seconds for as
long as it says `running`**, which is the one state in which you were most sure you had got it
right.

## The fields this reader draws that the other page does not

### `steps[]` — one dot per job

Drawn only while the top-level `state` is `running`, immediately after the progress bar:

```
⠦ deploy ▰▰▰▰▱▱▱▱ 2m00s/4m00s ●◐
```

Each entry contributes one glyph, chosen from its `state` alone, via `STEP_GLYPH`:

| `state` | glyph |
|---|---|
| `ok` | `●` green |
| `running` | `◐` amber |
| `fail` | `●` red |
| `cancel` | `○` grey |
| `skip` | `○` dim |
| anything else | `○` dim — the fallback, so an invented state is a quiet dot, not a crash |

**`name` is not drawn anywhere.** The status line reads only `state` out of each entry. Write the
names regardless — `gh-run-status.py` stores them, they are what makes the file legible to a
person debugging their own producer, and the field is the obvious place for a reader to start
using. Just do not design your pipeline around seeing them.

The reader imposes no limit on how many dots it draws. `gh-run-status.py` caps its own at four
and truncates each name to fourteen characters; that is a good ceiling to copy, because the line
drops whole segments when it runs out of width and a row of twelve dots is how you lose the
health light.

### `sha` and `head_in_run` — the `⚑ live` marker

This is the one field that makes an assertion, so it is worth being slow about. The marker
appears only when **all five** of these hold:

- `state` is `ok`; and
- `started_at` is missing, or more than 900 seconds ago — before that you get `✓ deploy`
  instead, and the tick wins; and
- `head_in_run` is exactly `false`; and
- `sha` is a non-empty string; and
- the branch has nothing unpushed (`ahead == 0` from `git status --porcelain=v2 --branch`)
  — with unpushed commits the `⎇ main↑2` next to the branch has already said it.

Then the cell is `⚑ live c6bbb16`, amber, linking to `url`. What it claims is narrow and useful:
**everything you have is pushed, and the thing that is live is not it.** It is the state in which
you conclude a deploy needs triggering when in fact your commit simply has not had its turn yet.

`gh-run-status.py` computes it as `git merge-base --is-ancestor HEAD <the run's headSha>`, so it
is an ancestry test and not an equality test: production being *ahead* of you is `true`, not
`false`.

**`false` is an assertion, not a safe default.** The reader tests `data.get("head_in_run") is
False`, so a missing key and a `null` both draw nothing, while `false` puts a claim on screen.
The poller itself omits the key when that `git` call fails, rather than guessing — copy that. If
your script does not know which commit is live, leave the field out; a wrong `⚑ live` sends
somebody to redeploy a commit that is already deployed.

There is a second reason for a local producer to leave it alone, and it is the more common one.
`head_in_run` is a comparison against **your HEAD, which moves after your file was written.** The
poller re-evaluates it every ninety seconds; a deploy script writes once. A script that shipped
HEAD and honestly wrote `"head_in_run": true` is holding a claim that quietly goes wrong with
your next commit — and the marker then stays silent in exactly the case it exists for. Write it
only if you rewrite the file whenever HEAD moves, which for most people means a git hook they did
not want.

### `title`

`gh-run-status.py` writes it (the run's `displayTitle`, cut to 60 characters) and **the status
line does not draw it anywhere.** It is in the file for the person reading the file and for other
readers of the format. Write it — a cache file you can `cat` and understand is worth the forty
bytes — but do not expect to see it, and do not put anything in it that has to be seen.

`label`, by contrast, is drawn verbatim, right next to the spinner. The poller lowercases its own
and takes the first word of the workflow name; nothing does that to yours. Keep it to one short
word for the same reason as the dots: whole segments are dropped when the line does not fit, and
`deploy` costs less than `deploy-production-europe`.

## The whole of `state`

`classify()` produces six values from GitHub's status and conclusion, and `main()` writes a
seventh directly:

| `state` | where it comes from | what the status line does with it |
|---|---|---|
| `running` | queued, in_progress, waiting, pending, requested | spinner, label, the progress bar, the step dots |
| `ok` | success, neutral | `✓ label` — **but only for 900 seconds after `started_at`**, then the `⚑ live` rule, then nothing |
| `fail` | failure, timed_out, startup_failure, action_required | `✗ label`, with no expiry in the reader — the poller retires its *own* failures after `FAIL_TTL = 6 * 3600` by writing `none`, and yours after `LOCAL_DONE_TTL` |
| `cancel` | cancelled | nothing |
| `skip` | skipped | nothing |
| `other` | any conclusion it has not heard of | nothing |
| `none` | written directly, with a `why` | nothing |

Two of those rows catch people out.

**`ok` without `started_at` draws nothing.** The test is `if data.get("started_at") and
time.time() - data["started_at"] < 900`, so a finished deploy that does not say when it started
never gets its tick — it goes straight to the `⚑ live` branch and usually falls out of that too.
If you want the fifteen minutes of `✓ deploy` that says the deploy worked, `started_at` is not
optional. (A permanent tick is decoration nobody reads, which is why it expires at all.)

**`none` is not an error, and it is the state you will emit most.** It means there is nothing
worth showing, and it carries a `why` for the person reading the file rather than for the line:
`no-gh`, `no-branch`, `gh-failed`, `no-runs`, `workflow-disabled`, `stale-fail`. Write `none`
with a `why` rather than deleting the file or writing an empty one — that is what keeps "nothing
to say" and "the producer is broken" distinguishable on disk at three in the morning.

And the rule for **a state you do not recognise**, in either direction: draw nothing.
`deploy_segment()` ends in a bare `return None` for precisely this reason. A reader that treats
an unknown state as failure paints a red cross on a project that simply has no CI, and a light
that is always red is indistinguishable from a broken light. This matters more than it sounds
like, because the vocabulary is expected to grow — the format is generic and GitHub Actions is
only its first producer.

## A deploy script that survives

Every path of this was run before it was pasted here: the happy one, a phase that fails, and a
`TERM` in the middle. The file it produces survives `gh-run-status.py` being run against it by
hand, and renders as `⠦ deploy ▰▰▰▰▱▱▱▱ 2m00s/4m00s ●◐`.

```bash
#!/usr/bin/env bash
set -euo pipefail

CACHE=~/.claude/statusline-cache
FILE="$CACHE/ghrun-you-notebook.json"        # owner-repo, from your origin remote
STARTED=$(date +%s)
SHA=$(git rev-parse --short=7 HEAD)
STEPS=""; PHASE=""

say() {                                    # say running | ok | fail
  local dots="$STEPS"
  [ -n "$PHASE" ] && dots="$dots,{\"name\":\"$PHASE\",\"state\":\"$1\"}"
  mkdir -p "$CACHE"
  cat > "$FILE.tmp$$" <<JSON
{"producer": "local",
 "state": "$1",
 "label": "deploy",
 "started_at": $STARTED,
 "updated_at": $(date +%s),
 "typical_seconds": 240,
 "sha": "$SHA",
 "steps": [${dots#,}]}
JSON
  mv -f "$FILE.tmp$$" "$FILE"              # atomic: never half a file on screen
}

phase() {                                  # phase <name> — whatever ran before it is done
  [ -n "$PHASE" ] && STEPS="$STEPS,{\"name\":\"$PHASE\",\"state\":\"ok\"}"
  PHASE=$1
  say running
}

trap 'say fail; exit 1' ERR INT TERM       # a killed deploy must not spin for half an hour

phase build   ; ./build
phase upload  ; ./upload
phase restart ; ./restart
say ok
```

What each part is doing there, since none of it is decoration:

- `producer` and `updated_at` are what make it survive the poller. Everything else on this page
  is downstream of those two lines.
- The trap marks **the phase that was in flight** as the failed dot, rather than a hard-coded
  guess at which step usually breaks. A dot that lies about where it stopped is worse than no
  dot, because it is the first thing you will act on.
- `mv` of a temporary file rather than writing in place, so a reader that catches the moment sees
  the old file or the new one and never half of each. Both readers parse the file on every
  redraw.
- `typical_seconds` is what turns the spinner into `▰▰▰▰▱▱▱▱ 2m00s/4m00s`. Leave it out and you
  get a plain elapsed time, which is still useful; the median of your last several real deploys
  is a better number than your estimate of it.
- No `head_in_run`, for the reason above: this script cannot keep that claim true.

**The file name** is `ghrun-<owner>-<repo>.json`, from the owner and repository Claude Code
reports for the window's workspace, with every character that is not a letter, digit, `-` or `_`
replaced by `-`. When it reports no repository, `deploy_segment()` returns before it builds a
path — the cell is not drawn and your file is never opened, and no producer can do anything about
that. Which repository's name to use when a script in one place deploys another is answered on
Clawdline's page, and the answer is the same here.

## `run-*.json` — the local `./test.sh`, and the one rule that is not `ghrun-`'s

Everything above is about a deploy. This is the other half of the same shape: **the run you
started yourself, on this machine, thirty seconds ago.** `run_segment()` draws it exactly as
`deploy_segment()` draws a deploy — a label, a bar from elapsed against typical, a verdict:

```
⠼ test ▰▰▰▱▱▱▱▱ compiling            a phase, drawn instead of the readout
⠴ test ▰▰▰▱▱▱▱▱ 2m00s/4m48s          no phase: elapsed against typical, as the deploy cell does
⠴ build ▰▰▰▰▰▰▰▰ 6m40s/4m48s         past typical: the bar and the readout go orange
✓ test                               for RUN_VERDICT_TTL after `updated_at`, then nothing
✗ test                               until the next run in this tree
```

It is a seventh file rather than a seventh use of `ghrun-`, and the reason is the file name.
`ghrun-<owner>-<repo>.json` is keyed by **the git remote**, so every worktree of one repository
is one slot — and a machine that runs several worktrees of one repository at once has them
overwriting each other's run, with the local run and the branch's real CI run fighting over the
same cell on top of that. **One run belongs to one tree**, so this one is keyed by the tree.

### The name is the tree

`run-<path>.json`, where `<path>` is the project directory with every character that is not a
letter, digit, `-` or `_` replaced by `-`, and then the last 48 of those. That is `path_key()`
in `statusline.py`, and it is the same key `health-*.json` and `backlog-*.json` already use — the
48-character truncation included, which Clawdline does not apply.

In a shell that is:

```bash
KEY=$(printf '%s' "$PWD" | tr -c 'A-Za-z0-9_-' '-' | tail -c 48)
```

Those two agree character for character on any path made of ASCII, which is what was checked.
They **disagree on a path containing anything else**: `tr` works on bytes and Python's
`str.isalnum()` knows about the rest of Unicode, so `~/code/專案` is `-Users-you-code-專案` to the
reader and `-Users-you-code---` to that one-liner, and the two open different files. If your
project paths are not ASCII, build the key the way the reader does rather than the way `tr` does.

The path is the one Claude Code reports as the window's `project_dir` — the repository, not
wherever the shell has been `cd`-ed to since. This is the same distinction `backlog_segment()`
takes one argument to enforce, and for the same reason: a session opened in one tree that ran
`./test.sh` in another would otherwise report the second tree's run under the first.

### What is the same as `ghrun-`

- **`state` is the only required field on the way in**, and every other key is optional. A
  reader must not lose the whole row because one field moved; `label` falls back to `run`.
- **`none`, and every state the reader has not heard of, draw nothing.** Never a cross for a
  word it has not learned — the vocabulary is expected to grow, and a red mark that is always
  wrong is indistinguishable from a broken light.
- **Write to a temporary name and `mv` it into place**, so a reader that catches the moment sees
  the old complete file or the new complete one and never half of each. Both readers parse the
  file on every redraw.
- **No heartbeat.** The bar is computed on every redraw from `started_at` and `typical_seconds`,
  so a file written once keeps moving on screen without you. Write at phase boundaries and at
  the end — but see the ceiling below, which is what a run longer than that costs you.
- **`label` is drawn verbatim**, next to the spinner, untranslated in every language. So is
  `phase`. Nothing in this feature adds a sentence anybody has to translate, which is the point
  of both being producer text. Keep them short for the same reason as before: whole segments are
  dropped when the line runs out of width, and `_run_text()` cuts both at 24 characters and
  turns control characters into spaces — this status line is exactly two lines, and a `\n` in a
  phase name would make it three.

### The three things that differ

**One: keyed by the working directory, not by the remote.** Above.

**Two: there is no `producer` field, and you must not invent one.** `ghrun-` needs one because it
has two writers racing — yours and `gh-run-status.py`, and the whole of that argument is
`local_deploy_holds()`. This file has one writer and no poller. There is nothing to lose a race
to, so there is nothing to declare.

**Three: the ceiling on a stuck spinner lives in the reader.** This is the rule worth reading
twice, because it is the one that does not exist anywhere else in this format. For `ghrun-`,
what eventually clears a `running` file that nobody retracted is the poller: thirty minutes
(`LOCAL_RUNNING_TTL`) after your last write, `local_deploy_holds()` stops holding and GitHub's
opinion lands on the next redraw. **Nothing polls `run-*.json`.** A run that was `kill -9`'d, or
a laptop that slept in the middle of a compile, would otherwise spin in that bar for as long as
the window stayed open. So `run_segment()` refuses to draw it:

```python
if time.time() - updated > (_epoch(data.get("stale_after")) or RUN_STALE_AFTER):
    return None
```

`RUN_STALE_AFTER` is 900 seconds, and it is what you get when the file has no `stale_after` of
its own. Three consequences for a producer:

- **`updated_at` is required, in epoch seconds, and a missing one is stale rather than fresh.**
  `_epoch()` returns 0 for an absent or unparseable value, which dates your file to 1970 — the
  safe direction. A producer that forgot the field draws nothing at all, rather than a spinner
  nobody can retract.
- **A run that legitimately takes longer than fifteen minutes must say so**, either by writing
  its own larger `stale_after` or by rewriting the same `running` payload with a fresh
  `updated_at` before the ceiling. This is the one case where the "no heartbeat" rule above does
  not hold, and it is the same exception the deploy section makes at thirty minutes.
- **The age comes from inside the file, never from its mtime.** Nothing touches this path
  behind your back — there is no poller to stamp it — but a checkout, a copy or an editor
  would, and none of those is somebody writing a run.

The rule is checked from both sides by `./verify.sh`: at exactly `stale_after` the row is still
drawn (the test is *older than*, not *at least*), one second past it draws nothing.

### The fields, and which of them are drawn

| field | required | what `run_segment()` does with it |
|---|---|---|
| `state` | **yes** | `running`, `ok`, `fail`. Anything else, including `none`, draws nothing |
| `updated_at` | **yes** | epoch seconds; the staleness ceiling and the tick's expiry are both measured from it |
| `label` | no | drawn verbatim beside the spinner or the mark; `run` when absent |
| `phase` | no | drawn verbatim **in place of the elapsed/typical readout** — "compiling" answers more at a glance than "2m00s/4m48s", and the bar has already said how far along this is |
| `started_at` | no | epoch seconds; without it there is no bar and no elapsed time, only the label |
| `typical_seconds` | no | what turns the spinner into `▰▰▰▱▱▱▱▱`. Without it you get a plain elapsed time |
| `stale_after` | no | seconds; `RUN_STALE_AFTER` (900) when absent or zero |
| `log` | no | an absolute path, made Cmd-clickable as `file://` — the whole cell becomes the link |
| `holder`, `tree` | no | **not drawn anywhere.** They are for the person who `cat`s the file, the way `title` is in `ghrun-`. Write them; do not expect to see them |

Two rows of `state` are worth being exact about, since neither matches the deploy cell:

- **`ok` expires `RUN_VERDICT_TTL` (900 seconds) after `updated_at`** — not after `started_at`.
  The deploy cell measures its tick from `started_at`, and "an `ok` without `started_at` draws
  nothing" is one of the two rows that catch people out up the page. This format requires
  `updated_at`, so there is always a timestamp and no reason to inherit the trap.
- **`fail` does not expire.** The deploy cell's poller retires its own failures after six hours;
  nothing retires this one but the next run in the same tree. Until then, "the last thing that
  ran here failed" is simply true about the tree you are standing in, and the next `./test.sh`
  overwrites it within a second of you doing something about it.

### A run script that survives

Every path of this was run before it was pasted here: the happy one, a phase that fails, and a
`TERM` in the middle. The last two both leave `{"state": "fail", "phase": "compiling"}` behind,
which is the point.

```bash
#!/usr/bin/env bash
set -euo pipefail

CACHE=~/.claude/statusline-cache
KEY=$(printf '%s' "$PWD" | tr -c 'A-Za-z0-9_-' '-' | tail -c 48)   # ASCII paths; see above
FILE="$CACHE/run-$KEY.json"
LOG=/tmp/my-tests.log
STARTED=$(date +%s)
PHASE=""

say() {                                      # say running | ok | fail
  mkdir -p "$CACHE"
  cat > "$FILE.tmp$$" <<JSON
{"state": "$1",
 "label": "test",
 "phase": "$PHASE",
 "started_at": $STARTED,
 "updated_at": $(date +%s),
 "typical_seconds": 288,
 "log": "$LOG",
 "tree": "$PWD"}
JSON
  mv -f "$FILE.tmp$$" "$FILE"                # atomic: never half a file on screen
}

phase() { PHASE=$1; say running; }           # one write per phase boundary
trap 'say fail; exit 1' ERR INT TERM         # a killed run must not spin for a quarter of an hour

phase compiling ; swift build
phase testing   ; swift test
say ok
```

The `exit` in the trap is not decoration: without it a `TERM` handler returns into the script,
which carries on and finishes by declaring success. That is the same measurement as in the
deploy example, and it is still true here.

There is no `producer` line in it, and that is deliberate — see above. There is no
`head_in_run` either: this file has no such field, because a local run makes no claim about what
is live.

## The other two files

Both are also produced by scripts the status line spawns itself. **There is no cron entry to
add** for any of this — `deploy_segment()`, `health_segment()` and `backlog_segment()` each start
their own detached process when their cache goes stale, which is the whole of the wiring.

### `health-*.json` — do not write this one

For this reader, hand-writing the health file buys nothing, and it is the one place where the
honest instruction is "configure it instead":

- With no `health` block carrying a `url` in that project's entry in
  `~/.claude/project-icons.json`, `health_segment()` returns before it opens any file. Whatever
  you wrote is not read.
- With one, the status line spawns `health-check.py` whenever the cache is older than 30 seconds
  (`sick`, `offline`, or never probed) or 120 seconds (`ok`), and that write lands on top of
  yours. **There is no `producer` guard on this file** — the registry is the guard.

So put the block in the registry: `url`, `label`, `site`, `expect`, `version_key`. The reasoning
about what to assert in `expect`, and what `version_key` gives you, is in
[connect.md](connect.md) and the README. Clawdline reads this file directly rather than probing,
which is why the shape is documented on its page — that is the clearest example of the split
between the two: it needs the file, this one needs the configuration.

One thing that belongs here rather than there: the light's freshness is judged from the **file's
mtime**, not from `checked_at`. Bright green within 3 minutes, dim green to 10, and after that
`⚠ prod ?12m00s` — the probe stopped, so nobody knows, which is not the same as fine.

### `backlog-*.json` — the guard is `source`, not `producer`

`backlog-status.py` looks in the repository for one of the path pairs in its `PROBES` list, runs
the script with `--json`, and stores whatever it printed. There is no registry key for a backlog:
a project either matches a probe or it does not.

Two are recognised today:

| interpreter | script | for |
|---|---|---|
| `backend/.venv/bin/python` | `backend/scripts/build_backlog_artifact.py` | a project with its own virtualenv |
| *(empty — the python running the status line)* | `tools/backlog.py` | **anything else** |

**The second one is the convention to follow.** An empty interpreter means "whatever python is
already running", so a repository needs no virtualenv and no dependency to be counted — which
matters, because a project that has a backlog and is never asked for it looks exactly like a
project that has none. Print one line of JSON on stdout with `source`, `total`, `lanes` and
`artifact`; `source` must be the absolute path of the file you counted, because that is what makes
the cache expire (below). `artifact` may be an empty string when there is no page to link to. The grouping into lanes is deliberately *not*
computed here — it is derived from severity and cost in the repository being asked, and a second
implementation of that rule would drift without anybody noticing, because the number on the
status line would go on looking correct.

If you produce this file some other way, what keeps it yours is `_backlog_stale()`, which asks
whether the source file changed rather than how old the cache is. **Write `source` or you get a
timer instead**, which is the difference between a count that refreshes the moment you edit the
file and one that refreshes a quarter of an hour later:

```python
if not data:
    return True
src = data.get("source")
if not src:
    return time.time() - (data.get("updated_at") or 0) > 900
try:
    return os.path.getmtime(src) > (data.get("source_mtime") or 0)
except OSError:
    return time.time() - (data.get("updated_at") or 0) > 3600
```

So: name your data file in `source`, and stamp `source_mtime` with that file's mtime **at the
moment you counted**. Get that right and the status line never spawns anything — it can see that
your count still holds. Leave `source` out and every redraw finds the cache stale, which means a
detached process every five seconds, for as long as the window is open, to recount something that
has not changed. It also means that whenever the source file does change, `backlog-status.py`
runs and writes its own answer over yours — so a producer that is not the one named in `PROBES`
has to recount before the next redraw, or be replaced by a count of zero.

The name is `backlog-<path>.json`, the project's directory with every character that is not a
letter, digit, `-` or `_` turned into `-` and then **the last 48 characters of that** — a
truncation Clawdline does not apply, so a project path longer than 48 characters is one of the
few places where the two readers look for different files. `health-*.json` is keyed the same way.

The cell draws only when `ok` is true and `total` is non-zero, and only `lanes.now` is coloured:
`≡53 now2`. `ok: false` exists on purpose — "this repository has no backlog" and "it has one and
reading it failed" are different facts, and a blank draws them identically.

## Checking a producer without disturbing your own status line

All of the above was verified this way, and it is worth doing before trusting a deploy script,
because the failure mode is silence.

Point your script's cache directory at a scratch path, run it, and then run the poller against
the same file by hand. If the file comes back byte for byte, the hold works:

```bash
mkdir -p /tmp/sl
./deploy.sh                              # with its CACHE line pointed at /tmp/sl
cp /tmp/sl/ghrun-you-notebook.json /tmp/before.json
python3 ~/.claude/gh-run-status.py "$PWD" /tmp/sl/ghrun-you-notebook.json
diff /tmp/before.json /tmp/sl/ghrun-you-notebook.json && echo "it held"
```

Delete `"producer": "local"` from the file and run that again: it will come back as GitHub's view
of the branch, or as `{"state": "none", "why": "no-runs"}`. That is the five-second disappearance,
reproduced in one command.

To see what a file actually draws, without waiting for a deploy and without touching
`~/.claude/statusline-cache`:

```python
import importlib.util, os
spec = importlib.util.spec_from_file_location("sl", os.path.expanduser("~/.claude/statusline.py"))
sl = importlib.util.module_from_spec(spec); spec.loader.exec_module(sl)
sl.CACHE_DIR = "/tmp/sl"                       # your scratch copy, not the real one
print(repr(sl.deploy_segment(os.getcwd(), {"owner": "you", "name": "notebook"}, 0)))
```

`deploy_segment` takes the third argument from `git status` — the number of commits you have not
pushed — so pass `0` to see what the `⚑ live` marker does and a positive number to watch it
correctly stay quiet.

The run cell takes the project directory and nothing else, and it opens no network and spawns
nothing, so it is the easiest of the three to drive by hand:

```python
sl.CACHE_DIR = "/tmp/sl"
print(repr(sl.run_segment("/Users/you/code/notebook")))     # writes /tmp/sl/run-<that path>.json
```

`./verify.sh` does exactly that, against its own scratch directory, for every rule stated above:
the staleness boundary from both sides, a `running` row with no `updated_at`, an unknown state
drawing nothing rather than a cross, a missing key not costing the whole row, and producer text
that cannot add a third line. If you change what this reader draws, that is the file that says
so.

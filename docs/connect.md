# Setting claude-bestiary up

**This page is written for an agent.** If somebody has pasted
`https://github.com/sainteye/claude-bestiary` at you and asked you to set it up, this is the whole
job: install it, put one block in their Claude Code settings, register the project you are in, and
verify. Then tell them what changed.

If you are a person: everything here is also in the README, in prose. This page is the ordered
procedure.

---

## What it is

A status line for Claude Code. Every project gets a small coloured creature and a name, so you
learn which window you are in the way you learn a face — before you have read anything. The rest
of the line is the things you would otherwise go and look up: branch and working tree, other
windows in the same project, a deploy in flight against how long that workflow usually takes,
whether the live service is answering and how far behind your commit it is, the backlog, and your
quota.

Local Python, no dependencies, about 55ms per redraw.

It also writes small JSON files under `~/.claude/statusline-cache/`, and those are read by
anything else that wants them —
[Clawdline](https://github.com/sainteye/clawdline) reads the same ones, so installing this
connects a project to that as well, without a second thing to keep current.

---

## 1. Install

```bash
git clone https://github.com/sainteye/claude-bestiary
cd claude-bestiary
./install.sh          # symlinks into ~/.claude, idempotent, safe to re-run
./verify.sh           # checks the links are still links, and that it runs
```

**Symlinks rather than copies**, deliberately: a copy needs a "remember to sync" step, that step
gets forgotten, and then the file you edited is not the file that runs — with nothing reporting
anything. Do not "helpfully" replace them with copies.

`install.sh` will not overwrite an existing `~/.claude/project-icons.json`. That file holds every
project's path on the machine and often the health endpoints of what they run; it is the user's,
not part of this repository.

## 2. Turn it on

In `~/.claude/settings.json`:

```json
{ "statusLine": { "type": "command",
                  "command": "bash ~/.claude/statusline-command.sh",
                  "refreshInterval": 2 } }
```

**Read that file, add this key, and write it back.** Do not overwrite it — it holds the user's
model, permissions, plugins and possibly hooks, and replacing it is a way to end somebody's
afternoon.

`refreshInterval` is not optional. Without it the line redraws only on an event — measured at
about twice a second on average with gaps up to 6.4 seconds — and a progress bar that sits still
looks exactly like a hang.

## 3. Register the project

Opening Claude Code in a project registers it automatically: a creature and a colour are generated
from its path, written down once, and fixed after that. So you may already be done.

To give it a real name, an emoji for the tab title, or a hand-drawn mark:

```bash
python3 ~/.claude/project-icon.py show          # what this project has now
python3 ~/.claude/project-icon.py list          # everything registered
python3 ~/.claude/project-icon.py find          # look for a logo in this repository
```

If the machine has the `project-icon` skill, use it — drawing a 7x4 mark that still reads as the
product's logo is a judgement call, and that skill is the procedure for making it.

**Never write `~/.claude/project-icons.json` with an atomic replace of your own.** It is very
often a symlink into somebody's private repository, and `os.replace()` replaces the *path*, not
the file the path points at — so the link becomes an ordinary file, the repository's copy stops
being the one in use, and **neither side reports an error**. That happened for real on
2026-08-11. `project-icon.py` resolves the link before writing; use it.

## 4. The health check, if this project deploys something

Add a `health` block to this project's entry in the registry:

```jsonc
"health": {
  "url": "https://example.com/health",   // what to poll
  "label": "prod",                       // what to call it on the line
  "site": "https://example.com/",        // where a click should go
  "expect": { "status": "ok", "database": true },   // what a healthy answer says
  "version_key": "commit"                // the field carrying the deployed revision
}
```

`expect` is how it knows healthy from merely answering: a 200 that says `{"database": false}` is
not health, and a check that only looks at the status code will tell you everything is fine during
the outage. Make it assert something that is actually false when the thing is broken.

`version_key` is what gives you `● prod ↑3` — the live service is three commits behind the code
you are holding. If the project's health endpoint does not report its build, that is worth adding
to the project; it is one field and it answers "is what I am looking at deployed".

**Nothing needs a cron entry.** The status line spawns the background pollers itself and they
write into `~/.claude/statusline-cache/`. Registering the project is the whole of the wiring.

---

## Verify, and report what you did

Do not report success without evidence:

1. `./verify.sh` — paste its output.
2. `bash ~/.claude/statusline-command.sh` from inside the project — it should print a status line,
   not a blank and not a Python traceback. **A blank line is a failure**; this prints in red when
   it breaks precisely so that "not configured" and "broken" do not look the same.
3. `python3 ~/.claude/project-icon.py show` — the mark and name for this project.
4. If you added a `health` block: wait a few seconds after a redraw and show that
   `~/.claude/statusline-cache/health-*.json` exists and says what you expect.
5. Confirm `~/.claude/project-icons.json` is still a symlink if it was one before you started:
   `ls -l ~/.claude/project-icons.json`.

Then tell the user in plain terms what is now on their status line, anything you could not do, and
— if they also use Clawdline — that the files this writes are the ones it reads, so their bar
gained the same information for free.

**If any of this left you guessing, say which part.** That is a defect in the documentation.

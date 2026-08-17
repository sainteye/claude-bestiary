#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sum up token usage from the local transcripts: this month, this project, this model.

    python3 usage-report.py                    # this month, every project
    python3 usage-report.py --month 2026-07    # a given month
    python3 usage-report.py --project my-api   # one project (matched on the directory name)
    python3 usage-report.py --months           # one line per month
    python3 usage-report.py --json             # for another program

The data is `~/.claude/projects/<slug>/<session>.jsonl`, written by Claude Code itself. Every
assistant reply carries `message.usage` (input / output / cache read / cache write) plus
`message.model` and `timestamp`. **No API call, and no login.**

## Why tokens and not money

The transcripts hold quantities and no unit price. On 2026-08-12, working backwards from the
official `total_cost_usd` of eight running sessions to a unit price for one model
(`claude-opus-5`) gave $0.45 to $25.25 per MTok — **a factor of 55**. At least three reasons it
cannot be recovered:

  1. `total_cost_usd` **is reset to zero by `/compact`** — one session read $32.61 before and
     $5.07 after, with both halves still in the transcript.
  2. Requests over 200k of context are priced separately (the status line payload's
     `exceeds_200k_tokens` is exactly this), and the transcript does not record which band a
     given request landed in.
  3. Server-side tools like web search and web fetch are billed separately and are not tokens.

So this **deliberately prints no money**. A made-up unit price produces a number that looks
precise and can be wrong by a factor of 55, which is worse than no number — without one you go
and look it up, with one you do not.

For what was actually spent: the `$` at the bottom right of the status line is the official
figure (one session, reset by compaction), and the account-level figure is at
<https://claude.ai/new#settings/usage>.

## What the columns mean

- **Cache read** dominates, often two hundred times the output, because every turn resends the
  whole conversation. It is the cheapest per token and the largest by volume, which makes it the
  best single indicator of "how much happened this month".
- **Output** is the most expensive and the closest thing to "how much the model actually wrote".
- Subagents (`isSidechain`) count inside the same session. Those tokens are yours too.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import sys

PROJECTS = os.path.expanduser("~/.claude/projects")
DEFAULT_CACHE = os.path.expanduser("~/.claude/statusline-cache/usage-month.json")


def buckets():
    return dict(req=0, inp=0, out=0, cache_read=0, cache_write=0, sessions=set())


def scan(root=PROJECTS, roots=None):
    """Transcripts to individual usage records.

    **Deduplicate.** The same reply can appear twice after a continuation or a retry, so the key
    is `(message.id, requestId)` — the two fields Claude Code itself uses to line requests up.

    **One file is one session, and one session belongs to one project.** So the project is
    decided once per file rather than per record from `cwd`: after an agent `cd`s into
    `backend/` partway through, every later record's `cwd` is a subdirectory, and deciding per
    record invents `backend` / `frontend` / `terraform` as projects. The first version did that,
    and `backend` reached the top of the table.
    """
    seen = set()
    for path in glob.glob(os.path.join(root, "*", "*.jsonl")):
        session = os.path.basename(path)[:-6]
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        proj = [None]                     # this session's project, decided by the first record
        with fh:
            for line in fh:
                # Cheap string filter before json.loads: user messages are most of the file
                if '"output_tokens"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                msg = d.get("message") or {}
                usage = msg.get("usage") or {}
                if usage.get("output_tokens") is None:
                    continue
                model = msg.get("model") or "?"
                if model == "<synthetic>":
                    continue          # Claude Code's own message; no request was made
                key = (msg.get("id"), d.get("requestId"))
                if key in seen:
                    continue
                seen.add(key)
                if proj[0] is None:
                    proj[0] = project_of(d.get("cwd") or "", roots)
                cc = usage.get("cache_creation") or {}
                yield {
                    "ts": d.get("timestamp"),
                    "project": proj[0],
                    "session": session,
                    "model": model,
                    "inp": usage.get("input_tokens", 0) or 0,
                    "out": usage.get("output_tokens", 0) or 0,
                    "cache_read": usage.get("cache_read_input_tokens", 0) or 0,
                    "cache_write": (cc.get("ephemeral_5m_input_tokens", 0) or 0)
                                   + (cc.get("ephemeral_1h_input_tokens", 0) or 0),
                }


def local_month(ts):
    """ISO 8601 (UTC) to a local-time YYYY-MM.

    Months have to be cut in **local** time. Taipei is UTC+8, so anything before eight in the
    morning is still the previous day in UTC — cutting months in UTC puts the first few hours of
    a month into the one before it.
    """
    if not ts:
        return "?"
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    return dt.astimezone().strftime("%Y-%m")


def known_roots():
    """Project roots registered in `project-icons.json`, longest first for prefix matching.

    Used instead of the transcript directory's slug, because a slug replaces `/` with `-` and
    cannot be reversed: the last segment of `-Users-you-code-my-app-ios` is `ios`, not
    `my-app-ios`.
    """
    reg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project-icons.json")
    try:
        with open(reg, encoding="utf-8") as f:
            paths = list((json.load(f).get("projects") or {}).keys())
    except (OSError, ValueError):
        paths = []
    return sorted((os.path.normpath(p) for p in paths), key=len, reverse=True)


def project_of(cwd, roots=None):
    """cwd to the project root's **absolute path**, not its name.

    A path rather than a name, so this lines up with the status line: all it holds is
    `workspace.project_dir`, and that same path is the key in `project-icons.json`. Keying on
    names would add a layer of guessing, and would merge two same-named directories in different
    places. `basename` is applied when displaying.
    """
    if not cwd:
        return "?"
    cwd = os.path.normpath(cwd)
    for r in roots or []:
        if cwd == r or cwd.startswith(r + os.sep):
            # The home directory entry is a catch-all; it is not a project root
            if r != os.path.expanduser("~"):
                return r
            break
    return cwd


def add(acc, rec):
    acc["req"] += 1
    for k in ("inp", "out", "cache_read", "cache_write"):
        acc[k] += rec[k]
    acc["sessions"].add(rec["session"])


def human(n):
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return "%.1f%s" % (n / div, unit)
    return str(int(n))


def tokens_of(a):
    """Total tokens for one bucket.

    **Which denominator hardly changes the answer**: one project's share of a month measured
    44.7% by output, 45.1% by total tokens and 43.9% by request count — under 1.5 percentage points
    apart. So take the least arbitrary one, every token added up, and do not argue about it.
    """
    return a["out"] + a["inp"] + a["cache_read"] + a["cache_write"]


def write_cache(path, month, by_project, total):
    """The small file the status line reads. **The status line never scans transcripts** — 215
    of them take 3.5 seconds, and one redraw has a budget of 55ms. The same arrangement as
    deploy and health: read a cache, spawn a background process when it is stale.
    """
    tot = tokens_of(total) or 1
    payload = {
        "month": month,
        "generated_at": int(datetime.datetime.now().timestamp()),
        "total_tokens": tokens_of(total),
        "total_sessions": len(total["sessions"]),
        # Keyed by the project root's absolute path, matching workspace.project_dir
        "projects": {p: {"tokens": tokens_of(a),
                         "share": round(100.0 * tokens_of(a) / tot, 1),
                         "sessions": len(a["sessions"]),
                         "requests": a["req"]}
                     for p, a in by_project.items()},
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


def print_table(title, rows, total):
    """rows: [(name, acc)], already sorted."""
    if not rows:
        print("  (nothing)")
        return
    print("\n%s" % title)
    print("  %-16s %7s %7s %9s %9s %9s" %
          ("", "session", "req", "output", "input", "cache read"))
    for name, a in rows:
        share = (tokens_of(a) / total * 100) if total else 0
        print("  %-16s %7d %7d %9s %9s %9s   %4.1f%%" %
              (name[:16], len(a["sessions"]), a["req"], human(a["out"]),
               human(a["inp"] + a["cache_write"]), human(a["cache_read"]), share))


def main():
    ap = argparse.ArgumentParser(
        description="Local Claude Code usage report (tokens only, never money)")
    ap.add_argument("--month", help="YYYY-MM, defaults to this month")
    ap.add_argument("--project", help="only this project (matched on the directory name)")
    ap.add_argument("--months", action="store_true", help="one line per month")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--cache", nargs="?", const=DEFAULT_CACHE,
                    help="write the small file the status line reads (default %s)"
                         % DEFAULT_CACHE)
    args = ap.parse_args()

    month = args.month or datetime.datetime.now().strftime("%Y-%m")

    by_month = collections.defaultdict(buckets)
    by_project = collections.defaultdict(buckets)
    by_model = collections.defaultdict(buckets)
    total = buckets()

    want = args.project
    if args.cache:
        want = None            # a share needs a denominator, so the cache always scans everything

    roots = known_roots()
    for rec in scan(roots=roots):
        m = local_month(rec["ts"])
        proj = rec["project"]                 # an absolute path
        add(by_month[m], rec)
        if m != month:
            continue
        if want and os.path.basename(proj) != want and proj != want:
            continue
        add(by_project[proj], rec)
        add(by_model[rec["model"]], rec)
        add(total, rec)

    if args.cache:
        write_cache(args.cache, month, by_project, total)
        print("wrote %s (%s, %d projects, %s tokens in total)" %
              (args.cache, month, len(by_project), human(tokens_of(total))))
        return

    # Display name: the last path segment, plus its parent when two would otherwise look alike
    names = collections.Counter(os.path.basename(p) for p in by_project)
    def show(p):
        base = os.path.basename(p)
        return base if names[base] == 1 else os.sep.join(p.split(os.sep)[-2:])

    if args.json:
        def clean(d):
            return {k: (len(v) if k == "sessions" else v) for k, v in d.items()}
        json.dump({"month": month,
                   "total": clean(total),
                   "by_project": {show(k): clean(v) for k, v in by_project.items()},
                   "by_model": {k: clean(v) for k, v in by_model.items()},
                   "by_month": {k: clean(v) for k, v in sorted(by_month.items())}},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
        return

    if args.months:
        print("by month (local time)")
        print("  %-9s %7s %7s %9s %9s %9s" %
              ("month", "session", "req", "output", "input", "cache read"))
        for m, a in sorted(by_month.items()):
            print("  %-9s %7d %7d %9s %9s %9s" %
                  (m, len(a["sessions"]), a["req"], human(a["out"]),
                   human(a["inp"] + a["cache_write"]), human(a["cache_read"])))
        return

    scope = ", %s only" % args.project if args.project else ", every project"
    print("Claude Code usage - %s%s" % (month, scope))
    print("  %d sessions, %s requests, %s output tokens, %s tokens read from cache" %
          (len(total["sessions"]), human(total["req"]), human(total["out"]),
           human(total["cache_read"])))

    print_table("by project (share = its total tokens / every project's total tokens)",
                [(show(k), v) for k, v in
                 sorted(by_project.items(), key=lambda kv: -tokens_of(kv[1]))],
                tokens_of(total))
    print_table("by model",
                sorted(by_model.items(), key=lambda kv: -tokens_of(kv[1])),
                tokens_of(total))
    print("\n  No money is printed here: the transcripts hold quantities and no unit price, and"
          "\n  working one out backwards is wrong by up to 55x (the reasons are at the top of"
          "\n  this file). For real spend see the $ on the status line for one session, or the"
          "\n  usage page on claude.ai for the account.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A Claude Code status line: one generated pixel creature per project, and what this window is
doing.

Two lines (Claude Code's statusLine supports more than one):

    ▟█▙  my-api      ~/code/my-api  ⎇ main            Opus 5 (1M) · xhigh
    █▀█  add rate limiting to the upload handler       ctx 77% · $7.91 · 5h 24%

The four rows of pixels on the left are squeezed into two lines of text with half blocks
(▀ / ▄): the top half painted in the foreground colour, the bottom half in the background, so
one line of text is two rows of pixels. A creature's shape (ears / legs / where the eyes sit)
and its hue both come from the project's path — the first time a project is seen, the
combination least like everything already registered is chosen and written down, and it stays.

The registry is ~/.claude/project-icons.json. Edit it by hand and your version wins.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import unicodedata
from urllib.parse import quote, urlsplit

HOME = os.path.expanduser("~")
REGISTRY = os.path.join(HOME, ".claude", "project-icons.json")
CACHE_DIR = os.path.join(HOME, ".claude", "statusline-cache")

# ── palette ───────────────────────────────────────────────────────────────
# 16 hues spread around the wheel, each in a "pale" and a "strong" tone = 32 slots. There are
# fewer projects than slots, so every project's colour is unique; two that share a hue land on
# different tones — deep blue and chalk blue are told apart at a glance on a dark background,
# which 24 adjacent hues would not be.
HUES = [8, 28, 45, 62, 85, 118, 145, 165, 188, 205, 222, 242, 262, 282, 305, 330]
# tone → (body s, body l, limb s, limb l).
# The second tone is deliberately "chalk" rather than "stronger": dropping the lightness on a
# dark background makes the label unreadable, so it raises lightness and removes saturation
# instead. Vivid blue against chalk blue is distinguishable, and both are legible.
TONES = [(0.66, 0.62, 0.62, 0.44), (0.38, 0.80, 0.34, 0.62)]
EMOJI = list("🦊🐙🦉🐢🦋🐝🦕🐳🦩🐧🦑🐞🦔🐡🦜🐊🦭🐴🦚🐌🦈🐦🦦🐬🪼🦂🐜🦇🕊🦤")

RESET = "\x1b[0m"
NO_COLOR = bool(os.environ.get("NO_COLOR"))
USAGE_URL = "https://claude.ai/new#settings/usage"

# How many columns on the right to leave alone. **Not for looks**: the documentation says
# system notifications (MCP errors, auto-update, low-context warnings) **share the right of that
# same line**, and that "in a narrow terminal these notifications will truncate your output".
# COLUMNS is the width of the terminal, not the width available.
RIGHT_RESERVE = 4


def hsl_rgb(h, s, l):
    """h 0-360, s/l 0-1 → (r, g, b) 0-255."""
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = l - c / 2
    seg = int(h // 60) % 6
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][seg]
    return tuple(int(round((v + m) * 255)) for v in (r, g, b))


def rgb_hsl(rgb):
    r, g, b = [v / 255.0 for v in rgb]
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60, s, l


def parse_hex(s):
    s = (s or "").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return None


def text_palette(accent):
    """Derive the whole line's colours from the accent, so the text and the icon match."""
    h, s, _ = rgb_hsl(accent)
    return {
        "soft": hsl_rgb(h, min(s, 0.30), 0.58),    # path, branch
        "faint": hsl_rgb(h, min(s, 0.20), 0.44),   # model, cost
        "text": hsl_rgb(h, min(s, 0.32), 0.84),    # what it is doing: the second line's subject
    }


def palette(hue_idx, tone):
    """One project's whole colour set. The status line and the preview share it, so the two
    cannot drift apart."""
    h = HUES[int(hue_idx) % len(HUES)]
    bs, bl, ls, ll = TONES[int(tone) % len(TONES)]
    return {
        "body": hsl_rgb(h, bs, bl),          # body = this project's colour
        "limb": hsl_rgb(h, ls, ll),          # limbs = one step darker
        "soft": hsl_rgb(h, 0.26, 0.56),      # path, branch: same family, stepped back
        "faint": hsl_rgb(h, 0.18, 0.42),     # model, cost: further back
        "text": hsl_rgb(h, 0.30, 0.84),      # what it is doing: same family, bright enough to lead
    }


def fg(rgb):
    return "" if NO_COLOR else "\x1b[38;2;%d;%d;%dm" % rgb


def bg(rgb):
    return "" if NO_COLOR else "\x1b[48;2;%d;%d;%dm" % rgb


def paint(text, code):
    return text if NO_COLOR else code + text + RESET


DIM = "\x1b[2m"
BOLD = "\x1b[1m"


# ── creatures ─────────────────────────────────────────────────────────────
# 5 columns by 4 rows, mirrored. `ears` and `legs` are 3 independent pixels each (1..7, never
# empty), and `eye` picks which row the eyes sit in → 7 x 7 x 2 = 98 shapes, times 16 hues.
SHAPES = [(e, l, y) for e in range(1, 8) for l in range(1, 8) for y in (0, 1)]


def build_grid(ears, legs, eye):
    """0 = transparent, 1 = the body colour, 2 = the limb colour (one step darker)."""
    grid = [[0] * 5 for _ in range(4)]
    for c in range(3):
        if ears >> c & 1:
            grid[0][c] = grid[0][4 - c] = 2
        if legs >> c & 1:
            grid[3][c] = grid[3][4 - c] = 2
    for r in (1, 2):
        for c in range(5):
            grid[r][c] = 1
    eye_row = 2 if eye == 0 else 1
    grid[eye_row][1] = grid[eye_row][3] = 0
    return grid


def creature_cells(shape, body, limb):
    """The generated fallback creature, as cells that already hold colours."""
    grid = build_grid(*SHAPES[int(shape) % len(SHAPES)])
    look = {0: None, 1: body, 2: limb}
    return [[look[v] for v in row] for row in grid]


def art_cells(art):
    """A hand-drawn pixel image, as cells that already hold colours.

    art = {"bg": "#2F6B5E", "palette": {"W": "#EEF6F4"}, "rows": [".WWW.", ...]}
    `rows` is 4 strings; '.' or a space is the background (transparent when `bg` is absent).
    The rows need not be the same width, they are padded.
    """
    rows = art.get("rows") or []
    if len(rows) != 4:
        return None
    bgc = parse_hex(art.get("bg")) if art.get("bg") else None
    look = {}
    for ch, hexv in (art.get("palette") or {}).items():
        look[ch] = parse_hex(hexv)
    width = max([len(r) for r in rows] + [1])
    cells = []
    for r in rows:
        r = r.ljust(width)
        cells.append([bgc if ch in (".", " ", "·") else look.get(ch, bgc) for ch in r])
    return cells


def render_rows(cells, top, bot):
    """Squeeze two rows of pixels into one line of text. The top half is ▀ (foreground); with
    only a bottom half it is ▄."""
    out = []
    for c in range(len(cells[top])):
        t = cells[top][c]
        b = cells[bot][c] if c < len(cells[bot]) else None
        if t and b:
            out.append(fg(t) + bg(b) + "▀" + RESET)
        elif t:
            out.append(fg(t) + "▀" + RESET)
        elif b:
            out.append(fg(b) + "▄" + RESET)
        else:
            out.append(" ")
    return "".join(out)


# ── registry ──────────────────────────────────────────────────────────────
DEFAULT_OPTIONS = {
    "show_cost": True,      # show what this session has cost, on the right of the second line
    "show_limits": True,    # show the 5-hour and 7-day quota
    "right_reserve": 4,     # columns left for system notifications; raise it if output truncates
}

README = (
    "Each project's colour and pixel creature are generated from its path, written down the "
    "first time it is seen, and fixed after that. Edit any of it by hand: hue is 0-15, tone is "
    "0 (pale) or 1 (strong), shape is 0-97 (the combination of ears, legs and eyes), label is "
    "the name shown on the status line, and emoji is used for the tab title. Delete an entry "
    "and a fresh one is generated the next time you open that project."
)


def load_registry():
    try:
        with open(REGISTRY, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    opts = dict(DEFAULT_OPTIONS)
    opts.update(data.get("options") or {})
    projects = data.get("projects")
    return (projects if isinstance(projects, dict) else {}), opts


def save_registry(projects, options):
    """Write the registry atomically.

    **Resolve the symlink first.** `os.replace` replaces the path, not the file the path points
    at — so doing it to REGISTRY directly turns `~/.claude/project-icons.json` from a symlink
    into a real file, after which the version in the repository is no longer the one running.
    It happened for real on 2026-08-11, triggered by auto-registering a new project, and
    `verify.sh` is what caught it. The same illness as a bind-mounted config whose inode a
    deploy replaces.
    """
    payload = {"_readme": README, "options": options, "projects": projects}
    try:
        target = os.path.realpath(REGISTRY)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, target)
    except Exception:
        pass


def hue_gap(a, b):
    """The distance between two hue indices around the wheel (0..8)."""
    d = abs(a - b) % len(HUES)
    return min(d, len(HUES) - d)


def assign(path, projects):
    """Choose the (hue, tone, shape) least like everything already registered.

    In order: get the hue as far from the used ones as possible, then change tone when the hue
    is taken, then change shape only when the whole colour collides. The seed comes from the
    path, so the result is stable however the same set of projects is first encountered.
    """
    seed = int(hashlib.sha256(path.encode("utf-8")).hexdigest()[:12], 16)
    others = [p for p in projects.values() if isinstance(p, dict) and "hue" in p]
    taken_hue = [p["hue"] for p in others]
    taken_color = set((p.get("hue"), p.get("tone", 0)) for p in others)
    taken_shape = set((p.get("hue"), p.get("tone", 0), p.get("shape")) for p in others)

    best, best_score = None, None
    for i in range(len(HUES)):
        h = (seed + i) % len(HUES)
        dist = min([hue_gap(h, t) for t in taken_hue], default=len(HUES) // 2)
        for tn in range(len(TONES)):
            t = (seed // 16 + tn) % len(TONES)
            for j in range(len(SHAPES)):
                s = (seed // 512 + j) % len(SHAPES)
                score = (dist, (h, t) not in taken_color, (h, t, s) not in taken_shape, -j)
                if best_score is None or score > best_score:
                    best_score, best = score, (h, t, s)
                if score[1] and score[2]:
                    break
            if best_score[0] == len(HUES) // 2 and best_score[1]:
                break
        if best_score[0] == len(HUES) // 2 and best_score[1]:
            break

    hue, tone, shape = best
    taken_emoji = set(p.get("emoji") for p in others)
    emoji = next(
        (EMOJI[(seed + k) % len(EMOJI)] for k in range(len(EMOJI))
         if EMOJI[(seed + k) % len(EMOJI)] not in taken_emoji),
        EMOJI[seed % len(EMOJI)],
    )
    return {"label": os.path.basename(path) or path, "hue": hue, "tone": tone,
            "shape": shape, "emoji": emoji}


# ── what this window is doing ─────────────────────────────────────────────
def first_prompt(transcript, session_id):
    """The fallback until session_name exists: the first thing said in this stretch. Read once,
    then cached."""
    cache = os.path.join(CACHE_DIR, session_id + ".title")
    try:
        with open(cache, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except Exception:
        pass
    text = None
    try:
        read = 0
        with open(transcript, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                read += len(line)
                if read > 2_000_000:
                    break
                if '"type":"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = next(
                        (b.get("text") for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and b.get("text")),
                        None,
                    )
                if text:
                    text = text.strip().splitlines()[0].strip()
                    break
    except Exception:
        return None
    if text:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                f.write(text[:120])
        except Exception:
            pass
    return text


# ── width ─────────────────────────────────────────────────────────────────
def git_state(cwd):
    """Branch, ahead/behind, and the working tree, from one git call.

    `--porcelain=v2 --branch` returns all of it in one pass rather than three processes. Staged,
    unstaged, untracked and conflicted are counted separately, because **an untracked new file
    is the easiest thing to lose**: when a parallel session commits in the same worktree, HEAD
    only gets half of it.
    """
    # Count **files**, not presence: "something is uncommitted" and "eleven files are
    # uncommitted" are different facts, and only the second one decides whether to stop now.
    out = {"branch": "", "head": "", "ahead": 0, "behind": 0,
           "staged": 0, "unstaged": 0, "untracked": 0, "conflict": 0}
    try:
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        r = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain=v2", "--branch"],
            capture_output=True, text=True, timeout=2.0, env=env)
        if r.returncode != 0:
            return out
    except Exception:
        return out
    for line in r.stdout.splitlines():
        # The commit you are holding, which the health light needs to say how far production
        # is from it. **Free**: this command already prints it, so it costs no second process.
        if line.startswith("# branch.oid "):
            oid = line[13:].strip()
            out["head"] = "" if oid == "(initial)" else oid
        elif line.startswith("# branch.head "):
            head = line[14:].strip()
            out["branch"] = "" if head == "(detached)" else head
        elif line.startswith("# branch.ab "):
            for tok in line[12:].split():
                if tok.startswith("+"):
                    out["ahead"] = int(tok[1:])
                elif tok.startswith("-"):
                    out["behind"] = int(tok[1:])
        elif line[:1] in ("1", "2"):
            xy = line.split(" ", 2)[1]
            # One file can be both staged and unstaged (a partial add), and then both are
            # incremented — `git status` lists it under both headings too
            if xy[0] != ".":
                out["staged"] += 1
            if xy[1] != ".":
                out["unstaged"] += 1
        elif line[:1] == "?":
            out["untracked"] += 1
        elif line[:1] == "u":
            out["conflict"] += 1
    return out


# The spinner's frame comes from **the clock**, not from a call count. The status line is not
# redrawn at any guaranteed rate; on a clock, frequent redraws turn smoothly and infrequent ones
# do not sit on one frame pretending to move.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
STEP_GLYPH = {"ok": ("●", (110, 200, 130)), "running": ("◐", (235, 190, 90)),
              "fail": ("●", (230, 100, 100)), "cancel": ("○", (150, 145, 140)),
              "skip": ("○", (110, 106, 100))}


def github_desktop_url(repo, branch=None):
    """Open GitHub Desktop on this repository and branch. No link when it is not installed."""
    if not repo or repo.get("host") != "github.com":
        return None
    owner, name = repo.get("owner"), repo.get("name")
    if not owner or not name:
        return None
    if not os.path.isdir("/Applications/GitHub Desktop.app"):
        return None
    url = "x-github-client://openRepo/https://github.com/%s/%s" % (owner, name)
    if branch:
        url += "?branch=" + quote(branch, safe="")
    return url


def fmt_elapsed(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return "%dh%02dm" % (h, m)
    if m:
        return "%dm%02ds" % (m, s)
    return "%ds" % s


def deploy_segment(cwd, repo, ahead=0):
    """Deploy and CI status: a spinner mid-run, plus one dot per job.

    The status line **never touches the network** — it reads a cache file and, when that is
    stale, spawns a detached process to refresh it. So a redraw always costs one file read
    (which is how the whole thing stays around 85ms).
    """
    if not repo or not repo.get("owner") or not repo.get("name"):
        return None
    key = "%s-%s" % (repo["owner"], repo["name"])
    key = "".join(c if c.isalnum() or c in "-_" else "-" for c in key)
    path = os.path.join(CACHE_DIR, "ghrun-%s.json" % key)

    data, age = None, 1e9
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass

    running = bool(data and data.get("state") == "running")
    # Watch closely mid-run; do not pester GitHub when nothing is happening
    if age > (5 if running else 90):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            open(path, "a").close()
            os.utime(path, None)          # stamp first, so many windows do not all spawn one
            subprocess.Popen(
                [sys.executable, os.path.join(HOME, ".claude", "gh-run-status.py"), cwd, path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except Exception:
            pass

    if not data:
        return None
    state = data.get("state")
    label = data.get("label") or "ci"

    if state == "running":
        amber, dim_amber = (235, 190, 90), (170, 140, 80)
        frame = SPINNER[int(time.time() * 8) % len(SPINNER)]
        seg = paint(frame + " " + label, fg(amber))

        started, typical = data.get("started_at"), data.get("typical_seconds")
        if started:
            elapsed = max(0, time.time() - started)
            if typical:
                # The bar is based on how long this workflow usually takes. Redraws are slow
                # (0.5 a second on average, measured), so the bar is what carries the progress
                # — not the spinner.
                ratio = elapsed / float(typical)
                over = ratio > 1.0
                fill = min(8, int(round(min(ratio, 1.0) * 8)))
                bar = paint("▰" * fill, fg((225, 130, 90) if over else amber))
                bar += paint("▱" * (8 - fill), fg((90, 86, 80)))
                seg += " " + bar + paint(
                    " %s/%s" % (fmt_elapsed(elapsed), fmt_elapsed(typical)),
                    fg((225, 130, 90) if over else dim_amber))
            else:
                seg += paint(" " + fmt_elapsed(elapsed), fg(dim_amber))

        dots = ""
        for st in (data.get("steps") or []):
            g, c = STEP_GLYPH.get(st.get("state"), ("○", (110, 106, 100)))
            dots += paint(g, fg(c))
        return osc8(data.get("url"), seg + (" " + dots if dots else ""))

    if state == "fail":
        return osc8(data.get("url"), paint("✗ " + label, "\x1b[31m"))

    if state == "ok":
        # Success only shows for a while afterwards; a permanent tick is decoration nobody reads
        if data.get("started_at") and time.time() - data["started_at"] < 900:
            return osc8(data.get("url"), paint("✓ " + label, fg((110, 200, 130))))
        # One exception is always worth saying: **everything is pushed, and what is running is
        # not this version.** That is the state where you conclude a deploy needs triggering
        # when in fact your commit has simply not had its turn yet. Not shown when there are
        # unpushed commits, because ↑n already said it.
        if ahead == 0 and data.get("head_in_run") is False and data.get("sha"):
            return osc8(data.get("url"), paint("⚑ live " + data["sha"], fg((190, 160, 90))))
    return None


def looks_like_sha(v):
    """Is this a value `git` could be pointed at? Hex, and long enough to be unambiguous."""
    return 7 <= len(v) <= 40 and all(c in "0123456789abcdefABCDEF" for c in v)


def sha_in(value):
    """The revision inside a reported version string, if there is one to find.

    A service rarely reports a bare SHA. `git describe` — which is what most build scripts
    reach for — writes `v1.2.3-5-gd47a60c`, and a tree that was not clean when it was built
    adds `-dirty`. **Both still name an exact commit**, and refusing to look inside them would
    answer "cannot measure" to a question that has a precise answer.
    """
    v = value.strip()
    if v.endswith("-dirty"):
        v = v[:-6]
    if looks_like_sha(v):
        return v
    tail = v.rsplit("-g", 1)[-1] if "-g" in v else ""
    return tail if tail and looks_like_sha(tail) else ""


def version_gap(cwd, head, live):
    """How far the version production reports is from the commit you are holding.

    This is the question `↑n` next to the branch does *not* answer: that one compares you with
    `origin`, so it goes quiet the moment you push — while the thing serving requests can sit
    ten commits back for an hour. And it is not the deploy cell's question either, which knows
    only what CI last *built*. **Only the service can say what it is actually running**, so this
    is computed from what it reported, and drawn nowhere else.

        (nothing)   the same commit — production is you, and a mark that is always there is
                    decoration nobody reads
        ↑3          three of your commits are not live yet
        ↓2          production is two ahead of you — someone else deployed, go and pull
        ↑3↓2        the two have diverged
        @1.4.2      it reported something git cannot measure against — an unknown revision, a
                    semantic version, another repository. **Say what it is rather than nothing**:
                    a blank cell here reads as "in sync", which is the one thing it does not mean.

    The comparison is made here, in the foreground, rather than by the probe that fetched the
    version: **its other half is your local HEAD, which changes between probes.** Computed in
    the background it would be stale exactly when you care — in the minute after you commit.
    That costs nothing on the common path, where the two SHAs are equal and no process is run
    at all; the one `git rev-list` on the path that remains was measured at 5.9ms against a
    redraw budget of 55ms.
    """
    if not live:
        return None
    sha = sha_in(live)
    if head and sha:
        n = min(len(head), len(sha))
        if head[:n].lower() == sha[:n].lower():
            return None                     # the same commit; there is nothing to report
        try:
            env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
            r = subprocess.run(
                ["git", "-C", cwd, "rev-list", "--left-right", "--count", "%s...HEAD" % sha],
                capture_output=True, text=True, timeout=1.5, env=env)
            if r.returncode == 0:
                behind, ahead = (int(x) for x in r.stdout.split())
                # left = commits live has and you do not (you are behind), right = the reverse
                return ("↑%d" % ahead if ahead else "") + ("↓%d" % behind if behind else "") \
                    or None
        except Exception:
            pass
    # A revision this clone does not have (never fetched, a shallow clone, a different
    # repository) or not a revision at all. It is still worth naming.
    return "@" + live[:12]


def health_segment(entry, proj, cwd="", head=""):
    """Service health: one light.

    **A green light is alive, not painted on.** A tick that is always there looks exactly like a
    probe that stopped weeks ago — the same illness as an alert that is silent whether or not
    anything is wrong. So this light encodes both "is it well now" and "how recent is that
    judgement":

        ● prod        green   confirmed within two minutes, everything matched
        ● prod        grey    still fine, but the probe has been quiet for a while
        ● prod ↑3     green   well, and running three commits behind what you are holding
        ⚠ prod ?12m   yellow  the probe stopped, or never ran — **unknown, not fine**
        ✗ prod mongo=false  red   genuinely broken, and it says which key
        ⚠ prod ?(offline)   grey  our network is down, production is not

    The value of this comes from running on **your laptop**: outside the VM, outside the cloud,
    outside the CDN. A watchdog inside cannot catch "the whole machine is off, the network is
    down", because it is not running either at that point.
    """
    cfg = entry.get("health")
    if not isinstance(cfg, dict) or not cfg.get("url"):
        return None
    key = "".join(c if c.isalnum() or c in "-_" else "-" for c in proj)[-48:]
    path = os.path.join(CACHE_DIR, "health-%s.json" % key)

    data, age = None, 1e9
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass

    state = (data or {}).get("state")
    # Watch closely when it is broken; do not hammer production when it is fine
    if age > (30 if state in ("sick", "offline", None) else 120):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            open(path, "a").close()
            os.utime(path, None)      # stamp first, so many windows do not all spawn a probe
            subprocess.Popen(
                [sys.executable, os.path.join(HOME, ".claude", "health-check.py"),
                 json.dumps(cfg, ensure_ascii=False), path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except Exception:
            pass

    label = (data or {}).get("label") or cfg.get("label") or "prod"

    # Where a Cmd-click goes **depends on the state**: when it is well the site itself is what
    # you want, and when it is not the JSON is (the failing key is written in it). One address
    # for both sends you to the wrong place in one of the two cases.
    site = cfg.get("site")
    if not site:
        try:
            u = urlsplit(cfg["url"])
            site = "%s://%s/" % (u.scheme, u.netloc)
        except Exception:
            site = cfg["url"]

    if state == "sick":
        return osc8(cfg["url"], paint("✗ %s %s" % (label, (data.get("detail") or "")[:22]),
                                      "\x1b[31m"))
    if state == "offline":
        # Our network, not production. Do not alarm, and do not pretend to know it is well
        return osc8(site, paint("⚠ %s ?(offline)" % label, fg((150, 145, 140))))
    if state == "ok":
        # Which version is answering, against the one you are holding. Only on the healthy
        # path, and for a reason: when it is broken, **the key that failed is a better use of
        # the same width**, and it is already there. The mark is deliberately not alarm-coloured
        # — being a few commits ahead of production is the normal state of a working day, not a
        # fault, and a light that cries about the ordinary gets ignored when it matters.
        gap = version_gap(cwd, head, (data or {}).get("version"))
        tail = paint(" " + gap, fg((150, 145, 140))) if gap else ""
        if age <= 180:
            return osc8(site, paint("● " + label, fg((90, 200, 120))) + tail)   # just confirmed
        if age <= 600:
            return osc8(site, paint("● " + label, fg((110, 130, 115))) + tail)  # fine, ageing
        # Past here the probe itself has stopped, so its version is as unknown as its verdict.
        # **Do not print a distance measured from a fact nobody has checked in ten minutes.**
        # More than ten minutes without an update means the probe stopped. **This must not stay
        # green.**
        return osc8(cfg["url"], paint("⚠ %s ?%s" % (label, fmt_elapsed(age)),
                                      fg((235, 190, 90))))
    # never probed
    return osc8(cfg["url"], paint("⚠ %s ?" % label, fg((235, 190, 90))))


def share_segment(proj):
    """What share of this month's total tokens this project has taken.

    It answers "which of these projects is eating my quota". The `7d 95%` next to it says how
    much is left; this says **who spent it**, and you need both to decide anything.

    **The status line never scans transcripts**: 215 of them take 3.5 seconds, and a redraw has
    a budget of 55ms. The same arrangement as deploy and health — read a cache, spawn a detached
    process when it is stale.

    The denominator is every project's total tokens (input, output, cache reads and writes).
    Output or request count as the denominator gives nearly the same answer (45.1% / 44.7% /
    43.9% for one project in 2026-08), so it is not worth arguing about.
    """
    path = os.path.join(CACHE_DIR, "usage-month.json")
    data, age = None, 1e9
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass

    # Across a month boundary last month's share is a wrong answer, not an old one. Discard it.
    if data and data.get("month") != time.strftime("%Y-%m"):
        data = None

    # Refresh slowly when there is data (a share barely moves in a day) and retry sooner when
    # there is none. Without that second case, one failure blanks this cell for fifteen minutes
    if age > (900 if data else 60):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            open(path, "a").close()
            os.utime(path, None)      # stamp first, so many windows do not all start a scan
            subprocess.Popen(
                [sys.executable, os.path.join(HOME, ".claude", "usage-report.py"),
                 "--cache", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except Exception:
            pass

    if not data:
        return None
    share = ((data.get("projects") or {}).get(proj) or {}).get("share")
    if share is None:
        return None
    return "project %s%%" % (("%.1f" % share).rstrip("0").rstrip("."))


def fmt_until(epoch):
    """How long until the reset. 86% decides nothing; "2 days 6 hours left" decides things."""
    try:
        left = int(epoch) - int(time.time())
    except (TypeError, ValueError):
        return None
    if left <= 0:
        return None
    d, rem = divmod(left, 86400)
    h, m = divmod(rem // 60, 60)
    if d:
        return "%dd%dh" % (d, h)
    if h:
        return "%dh%02dm" % (h, m)
    return "%dm" % m


def sibling_sessions(transcript, session_id, window=600):
    """How many other Claude Code windows are alive in this project.

    When parallel sessions share a worktree, somebody else edits the same files and commits the
    same things without you knowing — and nothing on screen says so. Each session writes its own
    jsonl, and its mtime is enough evidence of being alive (file reads only, no extra process).
    """
    try:
        d = os.path.dirname(transcript)
        now = os.path.getmtime(transcript)
        n = 0
        for f in os.listdir(d):
            if not f.endswith(".jsonl") or f[:-6] == session_id:
                continue
            if now - os.path.getmtime(os.path.join(d, f)) < window:
                n += 1
        return n
    except Exception:
        return 0


def skip_escape(s, i):
    """The position just past an escape sequence.

    **CSI and OSC have to be told apart**: a colour is `ESC [ ... m`, a hyperlink is
    `ESC ] 8 ; ; URL BEL`. This used to look for the next `m` in both cases — and any `m` in a
    URL (github.com has one) ended it in the wrong place, miscounting the width and cutting
    inside the sequence.
    """
    if i + 1 >= len(s):
        return i + 1
    kind = s[i + 1]
    if kind == "[":                                   # CSI: ends on a letter
        j = i + 2
        while j < len(s) and not s[j].isalpha():
            j += 1
        return j + 1
    if kind == "]":                                   # OSC: ends on BEL or ESC backslash
        j = s.find("\x07", i)
        k = s.find("\x1b\\", i)
        if j == -1 and k == -1:
            return len(s)
        if j == -1 or (k != -1 and k < j):
            return k + 2
        return j + 1
    return i + 2


def _backlog_stale(data):
    """Whether this cache still holds — **judged on whether the source changed, not on age**.

    Three cases need a recount: no cache, a cache with no `source`, and a source newer than the
    cache. When the source cannot be stat'd — the repository moved, a mount went away — it falls
    back to an hour, so it cannot get stuck forever on a number that can never refresh.

    **A missing `source` is a timer, not an immediate recount.** It used to return True outright,
    and the payload written for a repository with no backlog at all has no `source` — so the very
    file whose comment says "the status line stops calling this" was the thing that guaranteed it
    kept calling. Measured 2026-08-19 on a repository without a probe: a fresh subprocess roughly
    every five seconds, per window, indefinitely. The same branch covers a genuinely old-format
    cache, which now upgrades within the window instead of on the next render.
    """
    if not data:
        return True
    src = data.get("source")
    if not src:
        return time.time() - (data.get("updated_at") or 0) > 900
    try:
        return os.path.getmtime(src) > (data.get("source_mtime") or 0)
    except OSError:
        return time.time() - (data.get("updated_at") or 0) > 3600


def backlog_segment(proj):
    """What is still owed: a number and a Cmd-clickable path.

        ≡ 53          grey   53 items on the list, none of them due now
        ≡ 53 now 2    red    two that should have been done — **that cell should be empty**

    Why it belongs here: a backlog has exactly one enemy, which is gradually not being looked
    at. It lives in a YAML file and an artifact page, and both have to be thought of before they
    are opened — so how visible "what is still owed" is depends on who remembers. On the status
    line, nobody has to.

    **The grouping is not computed here** (a `lane` is derived from severity and cost, in the
    repository's own code) — this only reads what `backlog-status.py` stored. Recomputing it in
    the status line would be a second implementation, and nobody notices that kind of drift.

    A project with no backlog returns None rather than 0: **"nothing outstanding" and "this
    project does not have this at all" are different facts**, and drawing them identically
    suggests a project has finished everything.

    **One argument on purpose.** This took `cwd` as well and probed that, while keying the cache
    on `proj` — and those two are not the same thing: `project_dir` is the repository the line is
    describing, `current_dir` is wherever the shell has been `cd`-ed to since. A session opened
    in one repository that ran commands in another therefore wrote the second repository's
    backlog into the first one's cache file, and the status line then reported it as the first
    one's for as long as that file stayed fresh (seen 2026-08-18: another repository's 46 items
    shown under Clawdline, which has no backlog at all). Taking one path removes the chance
    disagreeing rather than relying on the caller to pass matching ones.
    """
    key = "".join(c if c.isalnum() or c in "-_" else "-" for c in proj)[-48:]
    path = os.path.join(CACHE_DIR, "backlog-%s.json" % key)

    data, age = None, 1e9
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass

    # **The test is the source file's mtime, not elapsed time.** The first version copied the
    # deploy cell's ten-minute TTL, but that cell talks to the network and has to be throttled;
    # this one reads one local YAML and has no reason to poll — the cost was a change you had
    # just saved taking up to ten minutes to appear (measured 2026-08-16: thirteen items out of
    # date on screen). Now: recount when the source is newer, and never otherwise.
    #
    # `age > 5` prevents re-entry: from the moment the condition holds until the recount
    # finishes, every redraw would hit it — and the cache's timestamp is stamped first (below),
    # so no second process starts within five seconds.
    if _backlog_stale(data) and age > 5:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            open(path, "a").close()
            os.utime(path, None)      # stamp first, so many windows do not all spawn one
            subprocess.Popen(
                [sys.executable, os.path.join(HOME, ".claude", "backlog-status.py"), proj, path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except Exception:
            pass

    if not data or not data.get("ok") or not data.get("total"):
        return None

    lanes = data.get("lanes") or {}
    now = lanes.get("now") or 0
    url = "file://" + data["artifact"] if data.get("artifact") else None

    body = paint("≡%d" % data["total"], fg((150, 145, 140)))
    if now:
        # Anything here means something was missed. That is the lane's definition, not a
        # judgement made here.
        body += paint(" now%d" % now, "\x1b[31m")
    return osc8(url, body)


def osc8(url, text):
    """Wrap text in a Cmd-clickable hyperlink (OSC 8). iTerm2, Kitty and WezTerm support it."""
    if not url or NO_COLOR:
        return text
    return "\x1b]8;;%s\x07%s\x1b]8;;\x07" % (url, text)


def dwidth(s):
    """Terminal display width — full-width characters count as 2 — skipping escape sequences."""
    w, i = 0, 0
    while i < len(s):
        ch = s[i]
        if ch == "\x1b":
            i = skip_escape(s, i)
            continue
        if unicodedata.combining(ch):
            i += 1
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        i += 1
    return w


def clip(s, limit):
    """Truncate by display width, **stepping over ANSI codes**.

    Cutting inside an escape sequence makes the terminal print the remaining bytes as text —
    which is how a narrow window ends up showing `[38;2;...` (observed 2026-08-11).
    """
    if limit <= 1 or dwidth(s) <= limit:
        return s
    out, w, i = [], 0, 0
    while i < len(s):
        ch = s[i]
        if ch == "\x1b":                      # copied through verbatim, costs no width
            j = skip_escape(s, i)
            out.append(s[i:j])
            i = j
            continue
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > limit - 1:
            break
        out.append(ch)
        w += cw
        i += 1
    return "".join(out) + RESET + "…"


# ── main ──────────────────────────────────────────────────────────────────
def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}

    ws = data.get("workspace") or {}
    proj = ws.get("project_dir") or ws.get("current_dir") or data.get("cwd") or os.getcwd()
    cwd = ws.get("current_dir") or data.get("cwd") or proj
    session_id = data.get("session_id") or ""

    projects, options = load_registry()
    entry = projects.get(proj)
    if not isinstance(entry, dict) or "hue" not in entry or "shape" not in entry:
        entry = assign(proj, projects)
        projects[proj] = entry
        save_registry(projects, options)

    # Hand-drawn if there is one, otherwise the generated creature — so a new project has
    # something to look at on its first day
    cells = art_cells(entry["art"]) if isinstance(entry.get("art"), dict) else None
    if cells:
        accent = parse_hex(entry["art"].get("accent")) or next(
            (c for row in cells for c in row if c), (200, 200, 200))
        body = accent
        soft, faint, text = (text_palette(accent)[k] for k in ("soft", "faint", "text"))
    else:
        pal = palette(entry.get("hue", 0), entry.get("tone", 0))
        body = pal["body"]
        soft, faint, text = pal["soft"], pal["faint"], pal["text"]
        cells = creature_cells(entry.get("shape", 0), pal["body"], pal["limb"])
    spr_top = render_rows(cells, 0, 1)
    spr_bot = render_rows(cells, 2, 3)

    label = entry.get("label") or os.path.basename(proj)
    worktree = ws.get("git_worktree")
    if worktree:
        label = "%s ⑂%s" % (label, worktree)

    git = git_state(cwd)
    branch = git["branch"]

    dir_display = cwd.replace(HOME, "~", 1) if cwd.startswith(HOME) else cwd

    model = ((data.get("model") or {}).get("display_name")) or ""
    effort = ((data.get("effort") or {}).get("level")) or ""
    if data.get("fast_mode"):
        effort = (effort + " ⚡").strip()

    ctx = data.get("context_window") or {}
    used = ctx.get("used_percentage")
    cost = (data.get("cost") or {}).get("total_cost_usd")
    limits = data.get("rate_limits") or {}

    doing = data.get("session_name")
    if not doing and session_id and data.get("transcript_path"):
        doing = first_prompt(data["transcript_path"], session_id)
    if not doing:
        doing = "(this stretch has no name yet - /rename gives it one)"

    # Build the second line first; that is what says how much width the title has left
    env_parts = [paint(dir_display, fg(soft))]
    if branch:
        b = "⎇ " + branch
        # ↑ unpushed, ↓ behind. Work that is committed and not pushed is otherwise invisible.
        if git["ahead"]:
            b += "↑%d" % git["ahead"]
        if git["behind"]:
            b += "↓%d" % git["behind"]
        # Cmd-click opens GitHub Desktop on this repository and branch. The contract is in
        # GitHub Desktop's main.js: the hostname is matched against openrepo (lowercase), the
        # pathname minus its leading / is the repository URL, and branch goes in the query.
        env_parts.append(osc8(github_desktop_url(ws.get("repo"), branch),
                              paint(b, fg(soft))))
    # Each mark carries a file count: + staged, * changed, ? untracked, ! conflicted.
    # "something is uncommitted" and "eleven files are uncommitted" are different situations.
    flags = "".join("%s%d" % (mark, git[key]) if git[key] else ""
                    for mark, key in (("+", "staged"), ("*", "unstaged"),
                                      ("?", "untracked"), ("!", "conflict")))
    if flags:
        env_parts.append(paint(flags, "\x1b[33m" if git["conflict"] else fg(soft)))
    n_sib = sibling_sessions(data.get("transcript_path") or "", session_id)
    if n_sib:
        # How many other windows are alive here. With a shared worktree, nothing else says so.
        env_parts.append(paint("👥%d" % n_sib, fg((230, 160, 60))))
    hp = health_segment(entry, proj, cwd, git["head"])
    if hp:
        env_parts.append(hp)
    dep = deploy_segment(cwd, ws.get("repo"), git["ahead"])
    if dep:
        env_parts.append(dep)
    bl = backlog_segment(proj)
    if bl:
        env_parts.append(bl)
    if model:
        env_parts.append(paint(model + (" · " + effort if effort else ""), fg(faint)))
    env_s = paint("  ", fg(faint)).join(env_parts)

    # First line: which project, and what it is doing. Those are the two things you identify
    # while switching windows, so they share the top line.
    metrics = []          # (drop priority, content): higher goes first, 0 is never dropped
    if used is not None:
        c = "\x1b[31m" if used >= 85 else ("\x1b[33m" if used >= 65 else "\x1b[32m")
        metrics.append((0, paint("ctx %d%%" % used, c)))
    if options.get("show_cost") and cost:
        metrics.append((3, paint("$%.2f" % cost, fg(faint))))
    if options.get("show_limits"):
        for key, name, prio in (("five_hour", "5h", 2), ("seven_day", "7d", 1)):
            win = limits.get(key) or {}
            pct = win.get("used_percentage")
            if pct is None:
                continue
            c = "\x1b[31m" if pct >= 85 else ("\x1b[33m" if pct >= 60 else fg(faint))
            seg = paint("%s %d%%" % (name, pct), c)
            # Only count down to the reset when it is nearly full; below that, the number
            # does not help anyone decide anything
            left = fmt_until(win.get("resets_at")) if pct >= 60 else None
            if left:
                seg += paint("(%s)" % left, fg(faint))
            metrics.append((prio, osc8(USAGE_URL, seg)))   # Cmd-click opens the usage page
    if options.get("show_share", True):
        # Directly after 7d: that cell says how much is left, this one says who spent it
        sh = share_segment(proj)
        if sh:
            metrics.append((4, paint(sh, fg(faint))))

    # The right has to shrink in a narrow window. Dropped in order of "what changes a decision
    # least": the project share (which barely moves in a day), then cost (a Max plan is not
    # billed on it), then the 5-hour window, then the 7-day one. ctx always stays (prio 0).
    def join_metrics(items):
        return paint(" · ", fg(faint)).join(s for _, s in items)

    metrics_s = join_metrics(metrics)

    try:
        cols = int(os.environ.get("COLUMNS") or 120)
    except ValueError:
        cols = 120
    icon_w = len(cells[0]) + 1
    avail = max(24, cols - int(options.get("right_reserve", RIGHT_RESERVE)))

    line1 = spr_top + " " + paint(label, BOLD + fg(body)) + "  "
    line1 += paint(clip(doing, max(16, avail - icon_w - dwidth(label) - 3)), fg(text))

    if metrics_s:
        # When it does not fit, give way in order of importance: the model segment first, then
        # the numbers on the right by priority, and only then truncate the left. **Nothing is
        # ever cut in half** — half a percentage is worse than none.
        left_parts = list(env_parts)
        kept = list(metrics)

        def fits():
            return avail - icon_w - dwidth(env_s) - dwidth(metrics_s) >= 1

        if not fits() and len(left_parts) > 1:
            left_parts = left_parts[:-1]                  # the model and its effort
            env_s = paint("  ", fg(faint)).join(left_parts)
        while not fits() and len(kept) > 1:
            worst = max(range(len(kept)), key=lambda i: kept[i][0])
            if kept[worst][0] == 0:
                break
            kept.pop(worst)
            metrics_s = join_metrics(kept)
        if not fits():
            env_s = clip(env_s, max(8, avail - icon_w - dwidth(metrics_s) - 2))
        pad = max(1, avail - icon_w - dwidth(env_s) - dwidth(metrics_s))
        line2 = spr_bot + " " + env_s + " " * pad + metrics_s
    else:
        line2 = spr_bot + " " + env_s

    sys.stdout.write(line1 + "\n" + line2 + "\n")

    # This once also tried to tint iTerm2's tab colour (\033]6;1;bg;...) and the window title.
    # Removed: a Claude Code subprocess has no controlling terminal, open("/dev/tty") simply
    # fails, and that code ran every time, was swallowed by an except every time, did nothing,
    # and nobody would ever have noticed. Window-level identity is left to the terminal title
    # Claude Code sets itself (to take that over, set CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 —
    # but the writing has to be done by a layer that has a tty).


if __name__ == "__main__":
    main()

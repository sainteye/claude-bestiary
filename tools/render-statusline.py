#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screenshot the status line, from a real run of it.

    python3 tools/render-statusline.py docs/statusline.png

A fenced code block cannot show colour, and colour is most of what this line communicates — the
project's hue, amber for a run in flight, green for a service that answered a minute ago. A
monochrome sample of a coloured thing argues against itself.

So this runs `statusline.py` for real, against a temporary HOME holding a made-up project and
made-up cache files, converts the ANSI it prints into HTML, and screenshots that with headless
Chrome. Real output rather than a mock-up, for the same reason as `render-bestiary.py`: a picture
drawn by hand starts lying the first time the thing it depicts changes.

Chrome because there is no image library here and macOS will not screenshot a terminal without a
recording permission — a browser is the one renderer already installed that can lay out text.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# A terminal's 16 colours are only used for the few hard-coded ones (red for a failure, green for
# ctx). Everything else arrives as a truecolour escape and needs no table.
BASIC = {"31": "#e06c68", "32": "#6ec882", "33": "#ebbe5a", "2": None, "1": None}

def fake_home(tmp):
    """A HOME holding one project — a real git repository, a deploy in flight, a healthy service
    and a backlog.

    A real repository rather than a stub, because the branch and the file counts come from
    `git status` and there is no way to fake that half without faking git. Everything else is
    written as the cache files the status line actually reads, so a payload accepted here is a
    payload accepted anywhere.
    """
    claude = os.path.join(tmp, ".claude")
    cache = os.path.join(claude, "statusline-cache")
    os.makedirs(cache)

    project = os.path.join(tmp, "code", "my-api")
    os.makedirs(project)
    git = ["git", "-C", project, "-c", "user.email=d@e", "-c", "user.name=demo",
           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]
    subprocess.run(["git", "init", "-q", "-b", "main", project], capture_output=True)
    open(os.path.join(project, "README.md"), "w").write("demo\n")
    subprocess.run(git + ["add", "README.md"], capture_output=True)
    subprocess.run(git + ["commit", "-qm", "first"], capture_output=True)
    # A working tree with something in every column: changed, and untracked.
    open(os.path.join(project, "README.md"), "a").write("more\n")
    for n in ("handler.py", "notes.md", "tmp.log"):
        open(os.path.join(project, n), "w").write("x\n")
    subprocess.run(git + ["add", "handler.py"], capture_output=True)
    # A remote that is four commits behind, so the line has something to say about pushing.
    # `--set-upstream-to` needs the remote to exist as configuration, not just as a ref.
    subprocess.run(git + ["remote", "add", "origin", "https://github.com/you/my-api"],
                   capture_output=True)
    subprocess.run(git + ["update-ref", "refs/remotes/origin/main", "HEAD"], capture_output=True)
    subprocess.run(git + ["branch", "-q", "--set-upstream-to=origin/main", "main"],
                   capture_output=True)
    for i in range(4):
        open(os.path.join(project, "c%d" % i), "w").write("x\n")
        subprocess.run(git + ["add", "c%d" % i], capture_output=True)
        subprocess.run(git + ["commit", "-qm", "c%d" % i], capture_output=True)

    registry = {
        "options": {"show_cost": True, "show_limits": True, "show_share": True,
                    "right_reserve": 4},
        "projects": {
            project: {
                "label": "my-api", "emoji": "🐙", "hue": 9, "tone": 0, "shape": 35,
                "health": {"url": "https://example.com/health", "label": "prod"},
            }
        },
    }
    with open(os.path.join(claude, "project-icons.json"), "w") as f:
        json.dump(registry, f)

    import time as _t
    now = int(_t.time())
    key = project.replace("/", "-")[-48:]
    files = {
        "ghrun-you-my-api.json": {
            "state": "running", "label": "deploy", "started_at": now - 394,
            "typical_seconds": 656, "updated_at": now,
            "steps": [{"name": "test", "state": "ok"}, {"name": "build", "state": "running"}],
            "sha": "d47a60c", "url": "https://example.com/run",
        },
        "health-%s.json" % key: {"state": "ok", "label": "prod", "checked_at": now, "ms": 88},
        "backlog-%s.json" % key: {
            "ok": True, "total": 53, "lanes": {"now": 2}, "updated_at": now,
            "artifact": "/tmp/backlog.html", "source": "/tmp/backlog.yaml", "source_mtime": now,
        },
        "usage-month.json": {
            "month": __import__("time").strftime("%Y-%m"), "generated_at": now,
            "total_tokens": 1000, "total_sessions": 4,
            "projects": {project: {"tokens": 451, "share": 45.1, "sessions": 2, "requests": 90}},
        },
    }
    for name, payload in files.items():
        with open(os.path.join(cache, name), "w") as f:
            json.dump(payload, f)
    # The cache files must not look stale, or the line spawns background refreshers that overwrite
    # them with this machine's real state — and the picture would show whatever is deploying here.
    for name in files:
        os.utime(os.path.join(cache, name), None)

    # Two other windows alive in the same project. That count comes from the mtime of the other
    # sessions' transcripts, so the only way to show it is to have some.
    sessions = os.path.join(claude, "projects", "demo")
    os.makedirs(sessions)
    for name in ("demo.jsonl", "other-1.jsonl", "other-2.jsonl"):
        open(os.path.join(sessions, name), "w").write("")
    # The HOME itself, not the .claude inside it — statusline.py appends that part.
    return tmp, project


def run(home, project, columns):
    payload = {
        "workspace": {"current_dir": project, "project_dir": project,
                      "repo": {"host": "github.com", "owner": "you", "name": "my-api"}},
        "model": {"display_name": "Opus 5 (1M)"},
        "effort": {"level": "xhigh"},
        "context_window": {"used_percentage": 11},
        "cost": {"total_cost_usd": 2.75},
        "rate_limits": {
            "five_hour": {"used_percentage": 25},
            "seven_day": {"used_percentage": 86, "resets_at": now_plus(2 * 86400 + 43200)},
        },
        "session_name": "add rate limiting to the upload handler",
        "session_id": "demo",
        "transcript_path": os.path.join(home, ".claude", "projects", "demo", "demo.jsonl"),
    }
    env = dict(os.environ, HOME=home, COLUMNS=str(columns))
    env.pop("NO_COLOR", None)
    out = subprocess.run([sys.executable, os.path.join(REPO, "statusline.py")],
                         input=json.dumps(payload), text=True, capture_output=True, env=env)
    return out.stdout.rstrip("\n")


def now_plus(seconds):
    import time as _t
    return int(_t.time()) + seconds


ESC = re.compile(r"\x1b\[([0-9;]*)m|\x1b\]8;;(.*?)\x07")


def html(text):
    """ANSI to spans. Only what the status line actually emits: truecolour, a few basic codes,
    and OSC 8 hyperlinks (rendered as plain text — a picture cannot be clicked)."""
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    out, fg, bg, i = [], None, None, 0
    for m in ESC.finditer(text):
        out.append(span(esc(text[i:m.start()]), fg, bg))
        i = m.end()
        if m.group(1) is None:                       # an OSC 8 marker, no colour change
            continue
        codes = (m.group(1) or "0").split(";")
        j = 0
        while j < len(codes):
            c = codes[j]
            if c in ("", "0"):
                fg = bg = None
            elif c in ("38", "48") and codes[j + 1:j + 2] == ["2"]:
                rgb = "#%02x%02x%02x" % tuple(int(v) for v in codes[j + 2:j + 5])
                if c == "38":
                    fg = rgb
                else:
                    bg = rgb
                j += 4
            elif c in BASIC:
                if BASIC[c]:
                    fg = BASIC[c]
            j += 1
    out.append(span(esc(text[i:]), fg, bg))
    return "".join(out)


def span(text, fg, bg):
    if not text:
        return ""
    style = ";".join(x for x in ("color:%s" % fg if fg else "",
                                 "background:%s" % bg if bg else "") if x)
    return "<span style='%s'>%s</span>" % (style, text) if style else text


PAGE = """<!doctype html><meta charset=utf-8><style>
  html,body{margin:0;background:#101216}
  pre{margin:0;padding:26px 30px;font:16px/1.5 Menlo,monospace;color:#c9ccd2;
      white-space:pre;display:inline-block}
</style><pre>%s</pre>"""

# Every segment worth naming, as (the text to find, what it is). Found by searching the real
# output rather than by counting columns: a status line is full of characters whose advance is
# not one cell — emoji, box drawing, the dots after a job — and a line drawn from a guessed
# column points at the wrong thing without ever looking broken.
MARKS = [
    ("my-api ", "the project, at a glance"),
    ("add rate limiting", "what this window is doing"),
    ("~/code/my-api", "where it is working"),
    ("main\u21914", "branch, and 4 commits not pushed"),
    ("*1?2", "1 changed, 2 untracked"),
    ("\U0001f465" + "2", "2 more windows in this project"),
    ("\u25cf prod", "the service answered a minute ago"),
    ("deploy", "a run in flight"),
    ("6m34s/10m56s", "elapsed, against how long it usually takes"),
    ("\u226153", "53 things on the backlog"),
    ("now2", "2 of them overdue"),
    ("ctx 11%", "context used"),
]

# Only what a 112-column terminal still has room for. The right-hand metrics — cost, both quota
# windows, this project's share — give way first when the window narrows, which is a real
# behaviour rather than an omission, and the table in the README covers them. A diagram that
# labelled segments this terminal is too narrow to show would be teaching a line nobody has.

ANNOTATED = """<!doctype html><meta charset=utf-8><style>
  html,body{margin:0;background:#0e1013;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
  #wrap{display:inline-block;padding:34px 40px 26px}
  pre{margin:0;font:16px/1.6 Menlo,monospace;color:#c9ccd2;white-space:pre;display:inline-block}
  svg{display:block;overflow:visible}
  .lbl{font:13px -apple-system,BlinkMacSystemFont,sans-serif;fill:#8b93a1}
  .hi{fill:#c9ccd2}
</style><div id=wrap><pre id=line>%s</pre><svg id=ann></svg></div>
<script>
const MARKS = %s;
const pre = document.getElementById('line');

// Walk the text nodes once so a match can be turned into a Range wherever it lands, including
// across the colour spans — which is most of them, since a segment and its colour are the same
// thing here.
let flat = '', map = [];
(function walk(n){ for (const c of n.childNodes) {
  if (c.nodeType === 3) { map.push([flat.length, c]); flat += c.data; } else walk(c);
} })(pre);

function rectOf(needle) {
  const at = flat.indexOf(needle);
  if (at < 0) return null;
  const find = (pos) => { for (let i = map.length - 1; i >= 0; i--)
    if (map[i][0] <= pos) return [map[i][1], pos - map[i][0]]; };
  const [n1, o1] = find(at), [n2, o2] = find(at + needle.length - 1);
  const r = document.createRange();
  r.setStart(n1, o1); r.setEnd(n2, o2 + 1);
  return r.getBoundingClientRect();
}

const base = pre.getBoundingClientRect();
const found = [], missing = [];
for (const [needle, label] of MARKS) {
  const r = rectOf(needle);
  if (!r) { missing.push(needle); continue; }
  found.push({x: r.left + r.width / 2 - base.left, y: r.bottom - base.top, label});
}
found.sort((a, b) => a.x - b.x);

// Lay the labels out in rows, dropping to a new one whenever the last label in this row would
// still be under the next connector. Three rows is enough for this line; a fourth would be
// further from what it points at than it is worth.
const CH = 7.0, ROW = 30, TOP = 16;
const ends = [];
const svg = document.getElementById('ann');
let maxRow = 0;
for (const f of found) {
  let row = 0;
  while (row < 6 && (ends[row] || -1e9) + 14 > f.x - 6) row++;
  ends[row] = f.x + f.label.length * CH;
  maxRow = Math.max(maxRow, row);
  const y = TOP + row * ROW;
  svg.insertAdjacentHTML('beforeend',
    `<path d="M${f.x} 0 L${f.x} ${y - 9}" stroke="#39404d" fill="none" stroke-width="1"/>` +
    `<circle cx="${f.x}" cy="0" r="2" fill="#39404d"/>` +
    `<text class="lbl" x="${f.x - 4}" y="${y}">${f.label}</text>`);
}
if (missing.length) {
  // A label with nothing under it is worse than no label, and at this size nobody would spot
  // it. Written where the tool can read it back rather than into the console.
  document.title = 'MISSING ' + missing.join(' | ');
  svg.insertAdjacentHTML('beforeend',
    `<text class="lbl" x="0" y="${TOP + (maxRow + 1) * ROW}" fill="#e06c68">` +
    `not found: ${missing.join(', ')}</text>`);
  maxRow += 1;
}
svg.setAttribute('height', TOP + maxRow * ROW + 16);
svg.setAttribute('width', pre.getBoundingClientRect().width + 260);
document.title = 'ready';
</script>"""


def as_html(lines):
    return "\n".join(html(l) for l in lines.split("\n"))


def shoot(page_html, out, size):
    tmp = tempfile.mkdtemp()
    try:
        page = os.path.join(tmp, "p.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(page_html)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=2", "--window-size=%d,%d" % size,
                        "--virtual-time-budget=1500",
                        "--screenshot=" + os.path.abspath(out), "file://" + page],
                       capture_output=True, check=True)
        print("wrote %s" % out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    docs = os.path.join(REPO, "docs")
    plain = sys.argv[1] if len(sys.argv) > 1 else os.path.join(docs, "statusline.png")
    noted = sys.argv[2] if len(sys.argv) > 2 else os.path.join(docs, "statusline-annotated.png")

    tmp = tempfile.mkdtemp()
    try:
        home, project = fake_home(tmp)
        # A realistic terminal for the screenshot, and a wide one for the diagram — every
        # segment a label points at has to actually be on the line it points at.
        lines = run(home, project, 112)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not lines.strip():
        sys.exit("the status line printed nothing")

    body = as_html(lines)
    shoot(PAGE % body, plain, (1160, 104))
    shoot(ANNOTATED % (body, json.dumps(MARKS)), noted, (1240, 178))


if __name__ == "__main__":
    main()

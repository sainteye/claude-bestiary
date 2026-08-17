#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Manage a project's status line icon: find a logo, see what is drawn now, write a new one.

    python3 ~/.claude/project-icon.py find  [project dir]   # which logo files this repo has
    python3 ~/.claude/project-icon.py show  [project dir]   # what is drawn (enlarged + real size)
    python3 ~/.claude/project-icon.py set   [project dir] < art.json
    python3 ~/.claude/project-icon.py list                  # every project

art.json looks like this (4 rows, 5 to 8 wide, '.' = background):

    {"bg": "#2F6B5E", "accent": "#5CBBA1", "palette": {"W": "#EEF6F4"},
     "rows": [".WWWWW.", ".W...W.", ".W.W.W.", ".W...W."]}

accent is the status line's text colour. A logo's dark colour used directly for text is
unreadable on a dark terminal, so put the version lifted until it reads, with the brand's hue
kept.
"""
import glob
import importlib.util
import json
import os
import sys

HOME = os.path.expanduser("~")
spec = importlib.util.spec_from_file_location("sl", os.path.join(HOME, ".claude", "statusline.py"))
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

# Earlier patterns are more likely to be the product's actual face
PATTERNS = [
    "**/app/icon.svg", "**/app/apple-icon.*", "**/apple-touch-icon.png",
    "**/AppIcon*1024*.png", "**/AppIcon*.png", "**/AppIconDisplay*.png",
    "**/icon-512*.png", "**/logo.svg", "**/logo.png", "**/icon.svg",
    "**/favicon.svg", "**/favicon.png", "**/favicon.ico",
    "**/*logo*.png", "**/*logo*.svg",
]
SKIP = ("node_modules", "/.git/", "/.next/", "/dist/", "/build/", "/.venv/")


def find_logos(root, limit=8):
    seen, out = set(), []
    for pat in PATTERNS:
        for hit in sorted(glob.glob(os.path.join(root, pat), recursive=True)):
            if any(s in hit for s in SKIP) or hit in seen:
                continue
            if os.path.isfile(hit) and os.path.getsize(hit) > 200:
                seen.add(hit)
                out.append(hit)
            if len(out) >= limit:
                return out
    return out


def cells_of(entry):
    if isinstance(entry.get("art"), dict):
        return sl.art_cells(entry["art"]), sl.parse_hex(entry["art"].get("accent"))
    pal = sl.palette(entry.get("hue", 0), entry.get("tone", 0))
    return sl.creature_cells(entry.get("shape", 0), pal["body"], pal["limb"]), pal["body"]


def show(cells):
    """Enlarged (two characters per pixel), then the real status line size (half blocks, two
    lines)."""
    for row in cells:
        print("".join((sl.bg(c) + "  " + sl.RESET) if c else "  " for c in row))
    print()
    print(sl.render_rows(cells, 0, 1))
    print(sl.render_rows(cells, 2, 3))


def target(argv):
    d = os.path.abspath(argv[2]) if len(argv) > 2 else os.getcwd()
    projects, options = sl.load_registry()
    # Walk upwards, so this still works from a subdirectory
    probe = d
    while probe != "/":
        if probe in projects:
            return probe, projects, options
        probe = os.path.dirname(probe)
    return d, projects, options


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"

    if cmd == "list":
        projects, _ = sl.load_registry()
        for path in sorted(projects, key=lambda p: (0 if projects[p].get("art") else 1, p)):
            e = projects[path]
            cells, accent = cells_of(e)
            mark = "drawn " if e.get("art") else "auto  "
            print("%s  %s  %-16s %s" % (sl.render_rows(cells, 0, 1), mark,
                                        e.get("label", ""), path.replace(HOME, "~")))
        return

    path, projects, options = target(sys.argv)

    if cmd == "find":
        hits = find_logos(path)
        if not hits:
            print("No logo file found. Draw from what the project is, or ask for an app icon.")
        for h in hits:
            print(h)
        return

    if cmd == "show":
        entry = projects.get(path)
        if not entry:
            print("%s is not in the registry yet (open Claude Code there once and it gets one)"
                  % path)
            return
        print("%s  ——  %s" % (entry.get("label", ""), path.replace(HOME, "~")))
        print("currently: %s" % ("hand-drawn" if entry.get("art") else "a generated creature"))
        print()
        show(cells_of(entry)[0])
        return

    if cmd == "set":
        art = json.load(sys.stdin)
        rows = art.get("rows") or []
        if len(rows) != 4:
            sys.exit("rows has to be exactly 4 rows, got %d" % len(rows))
        if len(set(len(r) for r in rows)) != 1:
            sys.exit("all 4 rows have to be the same width, got %s" % [len(r) for r in rows])
        if not sl.art_cells(art):
            sys.exit("that art is not the right shape")
        entry = projects.setdefault(path, sl.assign(path, projects))
        entry["art"] = art
        sl.save_registry(projects, options)
        print("written: %s" % path.replace(HOME, "~"))
        print()
        show(sl.art_cells(art))
        return

    sys.exit(__doc__)


if __name__ == "__main__":
    main()

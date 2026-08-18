---
name: project-icon
description: |
  Draw the coloured pixel icon that represents "this project" on the status line — find the
  repository's logo file, redraw it as a 7x4 pixel image, and write it into
  ~/.claude/project-icons.json, after which every window carries it on the top line.
  Use when the user says "draw an icon for this project", "change this project's icon", "draw an
  icon for project X", "the status line picture does not look like it", "here is the logo, follow
  this", or when a new project should be recognisable at a glance.
  Do not use when: changing which fields the status line shows (edit ~/.claude/statusline.py
  directly), or changing only the colour and not the picture (edit that entry's accent in the
  registry).
---

# Draw this project's status line icon

The goal is **recognising it at a glance while switching windows**. So the test is not "does it
look like the logo", it is **"shrunk to 7x4 pixels and glanced at, could it be confused with the
other twenty-three"** — colour is the strongest cue, shape second.

## Steps

### 1. Find the logo

```bash
python3 ~/.claude/project-icon.py find          # run inside the project directory
python3 ~/.claude/project-icon.py show          # what is drawn now
```

Open what it finds with the Read tool and look at it. **With no logo file, ask the user whether
there is an app icon** — a picture guessed from the project's name is harder to recognise than
the generated creature it would replace. Draw from the project's nature only if they have none.

### 2. Draw

The format is **4 rows** — this is not negotiable, the status line is two lines of text and one
line holds two rows of pixels — 5 to 8 columns wide, all four rows the same width. `.` is the
background; every other letter is looked up in `palette`.

```json
{"bg": "#2F6B5E", "accent": "#5CBBA1", "palette": {"W": "#EEF6F4"},
 "rows": [".WWWWW.", ".W...W.", ".W.W.W.", ".W...W."]}
```

- **`bg` fills the whole block**, so the result reads as a small app icon. Leave `bg` out and
  those cells show the terminal's background through them.
- **A row wider than its neighbours needs `bg`.** The four rows fold into two lines of text with
  half blocks, so the outer cells of a wide row — a creature's arms, or its ears — get half a
  block with the terminal showing through the rest, and read as fragments floating beside the
  shape rather than as arms. `bg` closes them into the block. Anything drawing the same registry
  at full height keeps the four rows as four rows and shows it correctly, so a shape that looks
  right there can still be wrong here.
- **`accent` is the status line's text colour, not the logo's main colour.** A brand's dark
  colour used for text is unreadable on a dark terminal — lift it until it reads, and keep the
  brand's hue. The greys for the path, branch and cost are derived from it automatically.
- **Give it enough width.** Five columns is often not enough. An arch drawn at 5 columns has its
  legs pinned to the outer edges and the dot in the middle merges with them, turning the whole
  thing into a box. Seven columns draws it.
- **Cut every detail.** 7x4 has no room for a gradient, text, or a thin line. What should be left
  is one shape and one or two colours.

### 3. Write it in and look

```bash
echo '{...}' | python3 ~/.claude/project-icon.py set
```

It prints both an enlarged version and the real status line size. **The two real-size lines are
the test** — something legible when enlarged is often mush at actual size. If it is mush, go back
to step 2 and simplify the shape further.

Changes take effect immediately (the status line re-reads the registry on every redraw); there is
no need to restart Claude Code.

## Check for colour collisions yourself

```bash
python3 ~/.claude/project-icon.py list
```

A project with no hand-drawn icon gets a generated creature whose colour is assigned from the
path and guaranteed not to collide (16 hues x 2 tones = 32 slots). **A hand-drawn `bg` and
`accent` are outside that mechanism** — run `list` before choosing, and look for anything already
close. Two apps of the same brand deliberately use the same shape with inverted colours; that one
is on purpose.

#!/bin/bash
# Symlink this repository's code into ~/.claude. Idempotent, safe to re-run.
#
# **Why symlinks rather than copies**: a copy needs a "remember to sync" step, that step will be
# forgotten, and the two then drift — and while they drift neither side reports anything, the
# copy you edited simply is not the one running. A symlink makes "the one in the repository" and
# "the one running" the same inode as far as the filesystem is concerned.
#
# **The registry is not one of these.** ~/.claude/project-icons.json holds the absolute path of
# every project on your machine, and often the health endpoints of the things you run. It is
# yours, it does not belong in a checkout of somebody else's repository, and this script will not
# touch one that already exists. With none there, the example is copied in as an ordinary file to
# start from.
#
# An existing real file is moved to <name>.pre-symlink rather than overwritten.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude"

FILES="statusline.py statusline-command.sh project-icon.py gh-run-status.py health-check.py usage-report.py backlog-status.py"

link() {
    local src="$1" dst="$2"
    if [ -L "$dst" ]; then
        [ "$(readlink "$dst")" = "$src" ] && { echo "  ✓ $dst"; return; }
        rm "$dst"
    elif [ -e "$dst" ]; then
        mv "$dst" "$dst.pre-symlink"
        echo "  ↳ kept the original as $(basename "$dst").pre-symlink"
    fi
    mkdir -p "$(dirname "$dst")"
    ln -s "$src" "$dst"
    echo "  + $dst"
}

echo "linking from $REPO into $DEST"
for f in $FILES; do link "$REPO/$f" "$DEST/$f"; done
# A skill's **directory cannot be a symlink** — measured 2026-08-11: Claude Code skipped the
# symlinked directory while scanning skills/, and listed the physical backup next to it instead.
# Keep the directory real and link only SKILL.md; any open() follows that fine.
mkdir -p "$DEST/skills/project-icon"
link "$REPO/skills/project-icon/SKILL.md" "$DEST/skills/project-icon/SKILL.md"

# The registry: left alone if you have one, seeded from the example if you do not.
REG="$DEST/project-icons.json"
if [ -e "$REG" ] || [ -L "$REG" ]; then
    echo "  = $REG (yours, untouched)"
else
    cp "$REPO/project-icons.example.json" "$REG"
    echo "  + $REG (copied from the example — edit the paths in it)"
fi

cat <<'EOF'

Add this to ~/.claude/settings.json:

  { "statusLine": { "type": "command",
                    "command": "bash ~/.claude/statusline-command.sh",
                    "refreshInterval": 2 } }

EOF
"$REPO/verify.sh"

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

[ "$FAIL" = 0 ] && echo && echo "all good" || { echo; echo "problems above"; exit 1; }

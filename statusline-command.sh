#!/bin/bash
# Claude Code's statusLine entry point. The implementation is ~/.claude/statusline.py.
#
# Two jobs only: find python3 (the status line's environment is not a login shell, so PATH is
# not guaranteed to be complete), and print one red line when it breaks — **a blank status line
# looks exactly like one that was never configured**, so breaking that way goes unnoticed.
#
input=$(cat)

PY=""
for cand in "$(command -v python3 2>/dev/null)" /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then PY="$cand"; break; fi
done

if [ -n "$PY" ] && out=$(printf '%s' "$input" | "$PY" "$HOME/.claude/statusline.py" 2>/dev/null) \
   && [ -n "$out" ]; then
    printf '%s\n' "$out"
else
    # verify.sh greps for "status line crashed" to tell a broken run from a working one.
    # Changing this wording means changing it there too.
    dir=$(printf '%s' "$input" | sed -n 's/.*"current_dir":"\([^"]*\)".*/\1/p')
    printf '\033[31mstatus line crashed\033[0m %s (run bash ~/.claude/statusline-command.sh to see why)\n' "${dir##*/}"
fi

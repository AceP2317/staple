#!/usr/bin/env bash
# Commit messages stay plain: no tool trailers, no bookkeeping lines, no third-person asides
#
# WHY THIS TEST EXISTS
# This repo is public, and its history is part of what a reader sees. Until 2026-11-10 every message
# ended with a link to a private working session, and some carried bookkeeping lines meant for the
# author's own records. They were reworded once; this keeps them from coming back.
#
# TWO HALVES. The control hands the scanner planted messages, each carrying one banned form, and one
# plain message: it must catch every planted one and pass the plain one. The sweep then reads every
# message on this branch.
#
# ponytail: the sweep runs before a commit is made, so a bad message is caught at the next commit, one
# late. The upgrade is a hook that reads the message as it is written.

set -uo pipefail
shopt -s nocasematch
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && { pwd -W 2>/dev/null || pwd; })"
BANNED='^(Claude-Session|Mistake-class|Designed-out|Instance-only):|by his call|his call|his pick'

# Reads messages on stdin, each opened by a line "@@<commit>", and prints every line carrying a banned form.
scan() {
  local commit="-" line
  while IFS= read -r line; do
    case "$line" in '@@'*) commit=${line#@@}; continue ;; esac
    if [[ "$line" =~ $BANNED ]]; then printf '  %s: %s\n' "$commit" "$line"; fi
  done
  return 0
}

blind=0
for planted in 'Claude-Session: https://example.com/session_x' 'Mistake-class: a-class-name' \
               'Designed-out: by a check' 'Instance-only: no time' 'Pages open light, by his call.'; do
  if [ -z "$(printf '@@planted\nA subject\n\n%s\n' "$planted" | scan)" ]; then
    echo "CONTROL BLIND: the scan missed a planted line: $planted"
    blind=1
  fi
done
if [ -n "$(printf '@@plain\nA subject\n\nA plain body that mentions a session and a class of mistake.\n' | scan)" ]; then
  echo "CONTROL BLIND: the scan flagged a plain message"
  blind=1
fi
[ "$blind" = 0 ] || exit 1
echo "control: 5 planted lines caught, and a plain message passed"

messages="$(git -C "$REPO_ROOT" log --format='@@%h%n%B' HEAD)" || { echo "FAILED: the history could not be read"; exit 1; }
count="$(git -C "$REPO_ROOT" rev-list --count HEAD)"
hits="$(printf '%s\n' "$messages" | scan)"
if [ -n "$hits" ]; then
  echo "FAILED: commit messages carry private lines:"
  echo "$hits"
  exit 1
fi
echo "sweep: $count commit messages read, none carries a private line"

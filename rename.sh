#!/usr/bin/env bash
# Rename the project in one step: file contents and paths.
#   ./rename.sh newname [DisplayName]
# newname: lowercase Python identifier (letters, digits, underscore).
# DisplayName defaults to newname with its first letter capitalised.
# The current name is read from pyproject.toml, so the script can be re-run.
set -euo pipefail
cd "$(dirname "$0")"

NEW="${1:-}"
if [[ ! "$NEW" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "usage: $0 newname [DisplayName]   (newname must match ^[a-z][a-z0-9_]*\$)" >&2
  exit 2
fi
NEW_DISPLAY="${2:-$(printf '%s' "${NEW:0:1}" | tr '[:lower:]' '[:upper:]')${NEW:1}}"
NEW_UPPER="$(printf '%s' "$NEW" | tr '[:lower:]' '[:upper:]')"

OLD="$(sed -n 's/^name = "\(.*\)"$/\1/p' pyproject.toml | head -1)"
OLD_DISPLAY="$(sed -n 's/^DISPLAY_NAME = "\(.*\)"$/\1/p' "$OLD/__init__.py" | head -1)"
OLD_UPPER="$(printf '%s' "$OLD" | tr '[:lower:]' '[:upper:]')"
[ -n "$OLD" ] && [ -n "$OLD_DISPLAY" ] || { echo "cannot detect current name" >&2; exit 1; }
[ "$OLD" != "$NEW" ] || { echo "already named $NEW"; exit 0; }

echo "renaming $OLD_DISPLAY/$OLD/$OLD_UPPER -> $NEW_DISPLAY/$NEW/$NEW_UPPER"

# 1. contents of every tracked-or-untracked text file outside .git
grep -rIl --null --exclude-dir=.git -e "$OLD" -e "$OLD_DISPLAY" -e "$OLD_UPPER" . \
  | xargs -0 perl -pi -e "s/\Q$OLD_DISPLAY\E/$NEW_DISPLAY/g; s/\Q$OLD_UPPER\E/$NEW_UPPER/g; s/\Q$OLD\E/$NEW/g"

# 2. paths, deepest first so parents are renamed after their children
find . -path ./.git -prune -o -depth -name "*$OLD*" -print | while IFS= read -r p; do
  base="$(basename "$p")"; dir="$(dirname "$p")"
  mv "$p" "$dir/${base//$OLD/$NEW}"
done

echo "done. Run: python3 -m unittest"

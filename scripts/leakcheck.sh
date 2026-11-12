#!/usr/bin/env bash
# Fail (exit 1) if the working tree OR any commit in git history contains a
# secret-shaped string or a personal path. Run before every commit and release.
#
# Generic patterns live below. Personal patterns (your username, hostnames,
# employer, clients) must NOT be published, so put them in a git-ignored file,
# one case-insensitive extended regex per line:
#     .leakcheck.local            (repo root, ignored by .gitignore)
#     $LEAKCHECK_PATTERNS         (any path, e.g. outside the repo)
# Also runs gitleaks / trufflehog when they are installed.
set -uo pipefail
cd "$(dirname "$0")/.."

# Written so that this file does not match itself.
GENERIC=(
  '\bs[k]-(ant-)?[A-Za-z0-9_-]{16,}'
  '\bgh[pousr]_[A-Za-z0-9]{20,}'
  'github_pa[t]_[A-Za-z0-9_]{20,}'
  '\b(AKI[A]|ASI[A])[0-9A-Z]{16}\b'
  'BEGIN ([A-Z]+ )*PRIVAT[E] KEY'
  '\bxox[abprs]-[A-Za-z0-9-]{10,}'
  '\bAIz[a][0-9A-Za-z_-]{35}'
  '\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.'
  '@gmai[l]\.com'
  '/Users/[A-Za-z][A-Za-z0-9._-]+'
  '/hom[e]/[a-z][a-z0-9._-]+/'
  '[A-Za-z]:\\\\Users\\\\[A-Za-z]'
  '\bG-[A-Z0-9]{10}\b'
  '\bUA-[0-9]{5,}-[0-9]+\b'
)

PRIVATE=()
for f in "${LEAKCHECK_PATTERNS:-}" .leakcheck.local; do
  [ -n "$f" ] && [ -f "$f" ] || continue
  while IFS= read -r line || [ -n "$line" ]; do
    [ -z "$line" ] || [[ "$line" == \#* ]] || PRIVATE+=("$line")
  done < "$f"
done

fail=0
check() {  # $1 = label, $2 = case flag ("" or "-i"), rest = patterns
  local label="$1" ci="$2"; shift 2
  local args=() p
  for p in "$@"; do args+=(-e "$p"); done
  # every file git would ship: tracked + untracked-but-not-ignored (binaries included via -a)
  local hits
  hits="$(git ls-files -z -co --exclude-standard | grep -zv '^\.leakcheck\.local$' \
          | xargs -0 grep -HnaoE $ci "${args[@]}" 2>/dev/null | cut -c1-160)"
  if [ -n "$hits" ]; then
    echo "LEAK ($label, tree):"; echo "$hits" | sed 's/^/  /'; fail=1
  fi
  if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
    hits="$(git log --all -p --text --format=fuller 2>/dev/null | grep -naoE $ci "${args[@]}" | cut -c1-160)"
    if [ -n "$hits" ]; then
      echo "LEAK ($label, git history):"; echo "$hits" | sed 's/^/  /'; fail=1
    fi
  fi
}

check generic "" "${GENERIC[@]}"
if [ "${#PRIVATE[@]}" -gt 0 ]; then
  check private -i "${PRIVATE[@]}"
  echo "leakcheck: ${#GENERIC[@]} generic + ${#PRIVATE[@]} private patterns"
else
  echo "leakcheck: ${#GENERIC[@]} generic patterns (no private pattern file found)"
fi

for tool in gitleaks trufflehog; do
  command -v "$tool" >/dev/null 2>&1 || { echo "leakcheck: $tool not installed, skipped"; continue; }
  if [ "$tool" = gitleaks ]; then gitleaks detect --source . --no-banner --redact || fail=1
  else trufflehog git "file://$PWD" --no-update --fail || fail=1; fi
done

if [ "$fail" -ne 0 ]; then echo "leakcheck: FAIL"; exit 1; fi
echo "leakcheck: PASS"

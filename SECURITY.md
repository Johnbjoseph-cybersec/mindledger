# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
"Report a vulnerability" button on this repository (Security advisories),
not in a public issue. Include steps to reproduce and the version you ran.
You should get a first response within 7 days.

## What MindLedger touches

- **Reads** Claude Code transcripts (`~/.claude/projects/**/*.jsonl`) and
  configuration under `~/.claude`. It never modifies them, except during an
  explicit `mindledger restore --yes`, which first keeps a `.bak-<timestamp>`
  copy of every file it overwrites.
- **Writes** only under `~/.mindledger` (or `$MINDLEDGER_HOME`), plus the
  scheduler unit/plist when you run `mindledger schedule install`.
- **Makes no network calls.** No telemetry, no update checks, no CDN in the
  report. The HTML report ships a Content-Security-Policy that blocks all
  external requests.

## Secret handling

Backups pass every text file through a scrubber that redacts common credential
shapes (provider API keys, cloud access keys, tokens, private keys, JWTs,
`password=`-style assignments) and any string value under a sensitive-looking
JSON key such as `env.SOME_API_TOKEN`. The scrubber is a safety net, not a
guarantee: treat your vault and its zips as private data and do not publish
them.

`restore` rejects zip entries with absolute paths or `..` segments and never
writes outside the target agent home.

## Supported versions

Only the latest release receives fixes.

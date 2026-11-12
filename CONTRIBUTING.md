# Contributing

Thanks for helping. A few ground rules keep this project small and safe.

1. **Standard library only.** No runtime dependencies. Python 3.9+, macOS and Linux.
2. **No network calls**, ever, in the tool or the generated report.
3. **Tests use synthetic data only.** Never commit real transcripts, stats,
   ledgers or screenshots of real usage. Add fixtures under `tests/fixtures/`
   or extend `scripts/make_demo_data.py`.
4. **Every derived number states its formula** in the report footer or note.

## Development

```sh
python3 -m unittest            # full suite, runs in under a second
bash scripts/leakcheck.sh      # must pass before every commit
python3 scripts/make_demo_data.py /tmp/ml-demo && \
  python3 -m mindledger report --projects /tmp/ml-demo --out /tmp/ml-demo.html
```

`scripts/leakcheck.sh` scans the working tree and the whole git history for
secrets and personal paths. You can add your own private patterns (your
username, hostnames, employer) in a git-ignored `.leakcheck.local` file, one
extended regex per line, so they are checked without being published.

## Pull requests

- One focused change per PR, with a test that fails without it.
- Keep the CLI output and file formats backward compatible; the ledger is
  append-only and old rows must stay readable.

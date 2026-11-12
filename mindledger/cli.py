"""Command-line entry point: `mindledger <command>`."""
import argparse
import datetime
import json
import os
import sys

from . import DISPLAY_NAME, NAME, __version__
from . import backup as backup_mod
from . import config, export, ledger, report, scan as scan_mod, schedule, search as search_mod
from .prices import estimate, load_prices


def _stats(args):
    """Load stats from --stats, or scan the transcript roots now."""
    if getattr(args, "stats", None):
        return scan_mod.load_json(args.stats)
    roots = config.project_roots(getattr(args, "projects", None))
    return scan_mod.scan(roots, use_utc=getattr(args, "utc", False))


def _summary(stats):
    t = stats["totals"]
    return ("tokens=%s (input %s, output %s, cache read %s, cache write %s) sessions=%s messages=%s days=%s"
            % (f"{t['tokens']:,}", f"{t['input']:,}", f"{t['output']:,}", f"{t['cache_read']:,}",
               f"{t['cache_creation']:,}", f"{t['sessions']:,}", f"{t['messages']:,}", t["days_active"]))


def cmd_scan(args):
    stats = _stats(args)
    out = args.out or config.path_in_home("stats.json")
    scan_mod.write_json(stats, out)
    print("scan: %s" % _summary(stats))
    print("scan: %d files, %d duplicate records skipped, %d unreadable lines -> %s"
          % (stats["scan"]["files"], stats["scan"]["duplicates_skipped"], stats["scan"]["bad_lines"], out))
    if args.cost:
        est = estimate(stats["models"], load_prices(args.prices))
        print("cost: $%s (%s; %s tokens unpriced)" % (f"{est['total_usd']:,.2f}", est["label"],
                                                       f"{est['unpriced_tokens']:,}"))
    return 0


def cmd_ledger(args):
    path = args.ledger or config.path_in_home("ledger.jsonl")
    if args.show:
        for row in ledger.load_rows(path)[-args.show:]:
            print("%s  %-10s total=%s delta=%s" % (row.get("ts"), row.get("status"),
                                                  f"{row.get('total', 0):,}", row.get("delta")))
        return 0
    row = ledger.append(_stats(args), path)
    msg = ledger.describe(row)
    print(msg, file=sys.stderr if row["status"] == "REGRESSION" else sys.stdout)
    return 3 if (row["status"] == "REGRESSION" and args.strict) else 0


def cmd_backup(args):
    home = args.agent_home or config.agent_home()
    vault = args.vault or config.path_in_home("vault")
    rep = backup_mod.mirror(home, vault)
    path, pruned = backup_mod.make_zip(vault, keep=args.keep)
    red = ", ".join("%s=%d" % kv for kv in sorted(rep["redactions"].items())) or "none"
    print("backup: %d files mirrored, %d removed, %d skipped (over size cap); redactions: %s"
          % (rep["files"], rep["removed"], rep["skipped_large"], red))
    print("backup: snapshot %s (pruned %d, keeping %d)" % (path, len(pruned), args.keep))
    return 0


def cmd_restore(args):
    if not args.dry_run and not args.yes:
        print("restore: pass --dry-run to preview, or --yes to write files", file=sys.stderr)
        return 2
    home = args.agent_home or config.agent_home()
    vault = args.vault or config.path_in_home("vault")
    zp = args.snapshot
    if not zp:
        snaps = backup_mod.list_snapshots(os.path.join(vault, "snapshots"))
        if not snaps:
            print("restore: no snapshots in %s" % vault, file=sys.stderr)
            return 1
        zp = snaps[-1]
    actions = backup_mod.restore(zp, home, dry_run=args.dry_run)
    for action, member, target in actions:
        print("%-9s %s -> %s" % (action, member, target or "(refused: unsafe path)"))
    counts = {}
    for a, _, _ in actions:
        counts[a] = counts.get(a, 0) + 1
    print("restore%s: %s from %s" % (" (dry run, nothing written)" if args.dry_run else "",
                                      ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "nothing", zp))
    print("note: secrets were redacted at backup time; re-enter any REDACTED values after a real restore.")
    return 0


def cmd_report(args):
    stats = _stats(args)
    out = args.out or config.path_in_home("report.html")
    report.write(stats, out, private=args.private, show_cost=args.cost, price_file=args.prices, n_days=args.days,
                 today=datetime.date.fromisoformat(args.as_of) if args.as_of else None)
    print("report: %s" % out)
    return 0


def cmd_export(args):
    pub = export.public_stats(_stats(args))
    out = args.out or config.path_in_home("public.json")
    scan_mod.write_json(pub, out)
    print(json.dumps(pub, indent=1))
    print("export-public: %s" % out, file=sys.stderr)
    return 0


def cmd_schedule(args):
    log_dir = config.path_in_home("logs")
    lines = (schedule.install(log_dir, dry_run=args.dry_run) if args.action == "install"
             else schedule.uninstall(dry_run=args.dry_run))
    for line in lines:
        print(line)
    return 0


def cmd_search(args):
    db = args.db or config.path_in_home("search.db")
    if not args.no_update:
        added, fts = search_mod.build_index(db, config.project_roots(args.projects))
        if added:
            print("index: %d new chunks (%s)" % (added, "fts5" if fts else "no fts5, scan fallback"), file=sys.stderr)
    rows, mode = search_mod.search(db, args.query, limit=args.limit, force_fallback=args.fallback)
    for day, cwd, session, snip in rows:
        print("%s  %s  %s\n    %s\n" % (day or "-", os.path.basename(cwd or "") or "-", (session or "")[:8],
                                       " ".join(snip.split())))
    print("%d result(s) via %s" % (len(rows), mode), file=sys.stderr)
    return 0


def cmd_sync(args):
    """The scheduled job: scan, ledger, backup, report, public export. One lock."""
    config.ensure_dir(config.data_home())
    lock = config.path_in_home(".sync.lock")
    try:
        os.mkdir(lock)
    except FileExistsError:
        age = datetime.datetime.now().timestamp() - os.stat(lock).st_mtime
        if age < 6 * 3600:
            print("sync: another run holds the lock; skipping")
            return 0
        os.rmdir(lock)  # stale lock from a crashed run
        os.mkdir(lock)
    try:
        stats = _stats(args)
        scan_mod.write_json(stats, config.path_in_home("stats.json"))
        row = ledger.append(stats, config.path_in_home("ledger.jsonl"))
        print(ledger.describe(row))
        rep = backup_mod.mirror(config.agent_home(), config.path_in_home("vault"))
        zp, _ = backup_mod.make_zip(config.path_in_home("vault"), keep=args.keep)
        report.write(stats, config.path_in_home("report.html"), private=args.private)
        scan_mod.write_json(export.public_stats(stats), config.path_in_home("public.json"))
        if args.index:
            search_mod.build_index(config.path_in_home("search.db"), config.project_roots(args.projects))
        line = "- %s status=%s total=%s files=%d snapshot=%s\n" % (
            datetime.datetime.now().isoformat(timespec="seconds"), row["status"], row["total"],
            rep["files"], os.path.basename(zp))
        with open(config.path_in_home("sync.log"), "a", encoding="utf-8") as fh:
            fh.write(line)
        print("sync: done -> %s" % config.data_home())
        return 3 if row["status"] == "REGRESSION" else 0
    finally:
        os.rmdir(lock)


def build_parser():
    p = argparse.ArgumentParser(prog=NAME, description="%s: a private ledger of your coding-agent usage." % DISPLAY_NAME)
    p.add_argument("--version", action="version", version="%s %s" % (NAME, __version__))
    sub = p.add_subparsers(dest="cmd", required=True)

    def src(sp, stats=True):
        sp.add_argument("--projects", action="append", metavar="DIR",
                        help="transcript root (repeatable). Default: $MINDLEDGER_PROJECTS or ~/.claude/projects")
        sp.add_argument("--utc", action="store_true", help="bucket days in UTC instead of local time")
        if stats:
            sp.add_argument("--stats", metavar="FILE", help="use a saved stats.json instead of scanning")

    s = sub.add_parser("scan", help="parse transcripts into stats.json")
    src(s, stats=False)
    s.add_argument("--out", metavar="FILE")
    s.add_argument("--cost", action="store_true", help="print a list-price-equivalent cost estimate")
    s.add_argument("--prices", metavar="FILE", help="JSON price rules (see prices.py)")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("ledger", help="append this scan to the never-decrease ledger")
    src(s)
    s.add_argument("--ledger", metavar="FILE")
    s.add_argument("--show", type=int, nargs="?", const=10, metavar="N", help="print the last N rows")
    s.add_argument("--strict", action="store_true", help="exit 3 when the total went down")
    s.set_defaults(fn=cmd_ledger)

    for name, fn, hlp in (("backup", cmd_backup, "mirror config into the vault and write a dated zip"),
                          ("restore", cmd_restore, "restore a snapshot (preview with --dry-run)")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("--agent-home", metavar="DIR", help="default: $MINDLEDGER_CLAUDE_HOME or ~/.claude")
        s.add_argument("--vault", metavar="DIR", help="default: ~/.mindledger/vault")
        if name == "backup":
            s.add_argument("--keep", type=int, default=30, help="dated zips to keep (default 30)")
        else:
            s.add_argument("snapshot", nargs="?", help="zip to restore (default: newest)")
            s.add_argument("--dry-run", action="store_true")
            s.add_argument("--yes", action="store_true", help="really write files")
        s.set_defaults(fn=fn)

    s = sub.add_parser("report", help="write the self-contained HTML report")
    src(s)
    s.add_argument("--out", metavar="FILE")
    s.add_argument("--private", action="store_true", help="embed only hashed project names")
    s.add_argument("--cost", action="store_true", help="add a list-price-equivalent cost section")
    s.add_argument("--prices", metavar="FILE")
    s.add_argument("--days", type=int, default=90, help="days shown in the daily chart")
    s.add_argument("--as-of", metavar="YYYY-MM-DD", help="treat this date as today (for the streak)")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("export-public", help="write a minimal public-stats JSON for a website")
    src(s)
    s.add_argument("--out", metavar="FILE")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("schedule", help="install or remove the every-2-hours sync job")
    s.add_argument("action", choices=["install", "uninstall"])
    s.add_argument("--dry-run", action="store_true", help="print what would be done")
    s.set_defaults(fn=cmd_schedule)

    s = sub.add_parser("search", help="full-text search over your sessions")
    s.add_argument("query")
    s.add_argument("--projects", action="append", metavar="DIR")
    s.add_argument("--db", metavar="FILE")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--fallback", action="store_true", help="force the non-FTS scan")
    s.add_argument("--no-update", action="store_true", help="do not refresh the index first")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("sync", help="scan + ledger + backup + report + export (what the schedule runs)")
    src(s, stats=False)
    s.add_argument("--keep", type=int, default=30)
    s.add_argument("--private", action="store_true")
    s.add_argument("--index", action="store_true", help="also refresh the search index")
    s.set_defaults(fn=cmd_sync)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

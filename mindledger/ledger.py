"""Append-only token ledger with a never-decrease guard.

Your lifetime token total is cumulative: it can only stay level or grow. If a
scan ever reports LESS than the previous ledger row, something upstream broke -
transcripts were deleted by a retention setting, a mounted directory vanished,
a glob stopped matching - and the drop must be shouted about, not quietly
written down as the new truth. Each row records what one run saw, so "what did
it say yesterday?" is always answerable from the file itself.
"""
import datetime
import json
import os

REGRESSION_BANNER = "TOTAL WENT DOWN"


def load_rows(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue  # tolerate a truncated final line from an interrupted write
    return rows


def last_row(path):
    rows = load_rows(path)
    return rows[-1] if rows else None


def high_water(path):
    """Largest total ever recorded (the guard compares against this, not just the
    last row, so two consecutive drops cannot hide behind each other)."""
    return max((int(r.get("total", 0)) for r in load_rows(path)), default=None)


def append(stats, path, now=None):
    """Append one row for this scan. Returns the row (status OK or REGRESSION)."""
    t = stats["totals"]
    total = int(t["tokens"])
    row = {
        "ts": (now or datetime.datetime.now().astimezone()).isoformat(timespec="seconds"),
        "total": total,
        "input": int(t.get("input", 0)),
        "output": int(t.get("output", 0)),
        "cache_read": int(t.get("cache_read", 0)),
        "cache_creation": int(t.get("cache_creation", 0)),
        "sessions": int(t.get("sessions", 0)),
        "messages": int(t.get("messages", 0)),
        "days_active": int(t.get("days_active", 0)),
    }
    prev = last_row(path)
    hw = high_water(path)
    row["delta"] = (total - int(prev["total"])) if prev else None
    if hw is not None and total < hw:
        row["status"] = "REGRESSION"
        row["regression"] = {"high_water": hw, "lost": hw - total,
                             "prev_ts": prev.get("ts") if prev else None}
    else:
        row["status"] = "OK"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


def describe(row):
    if row["status"] == "REGRESSION":
        r = row["regression"]
        return ("LEDGER: *** %s *** %s -> %s (lost %s tokens vs the high-water mark). "
                "Check whether transcripts were deleted or a source directory is missing."
                % (REGRESSION_BANNER, f"{r['high_water']:,}", f"{row['total']:,}", f"{r['lost']:,}"))
    d = row.get("delta")
    return "LEDGER: total=%s%s" % (f"{row['total']:,}",
                                   ("  delta=%+d" % d) if isinstance(d, int) else "  (first row)")

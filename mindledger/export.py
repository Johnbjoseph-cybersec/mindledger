"""Public stats: a tiny JSON you can embed on a personal website.

Allow-list by construction: only the fields below are ever written. No paths,
project names, machine names, model mix or cost figures.
"""
import datetime

PUBLIC_FIELDS = ("total_tokens", "sessions", "messages", "days_tracked",
                 "daily_avg_tokens_30d", "as_of", "schema")


def public_stats(stats, today=None):
    t = stats["totals"]
    days = stats.get("days", {})
    today = today or datetime.date.today()
    window = [(today - datetime.timedelta(days=i)).isoformat() for i in range(30)]
    last30 = sum(int(days.get(d, {}).get("tokens", 0)) for d in window)
    out = {
        "schema": 1,
        "total_tokens": int(t["tokens"]),
        "sessions": int(t["sessions"]),
        "messages": int(t["messages"]),
        "days_tracked": int(t["days_active"]),
        "daily_avg_tokens_30d": int(round(last30 / 30.0)),
        "as_of": today.isoformat(),
    }
    assert set(out) == set(PUBLIC_FIELDS)
    return out

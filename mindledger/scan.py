"""Parse Claude Code session transcripts (*.jsonl) into usage statistics.

Transcript facts this relies on:
  * one JSON object per line; usage lives on records with type == "assistant"
    under message.usage (input_tokens, output_tokens, cache_read_input_tokens,
    cache_creation_input_tokens);
  * one API response is written as SEVERAL records (one per content block) that
    share message.id and repeat the same usage, so tokens must be de-duplicated
    by message id or they are counted two to five times;
  * resumed, forked and copied-between-machines sessions repeat earlier records,
    so user records are de-duplicated by uuid and assistant records by message id
    across every root you scan. Point the scanner at several roots (for example
    a second machine's copied transcripts) and nothing is double-counted.

De-dup keys are kept as 64-bit BLAKE2b digests to bound memory on large
histories (collision odds are negligible below billions of records).
"""
import datetime
import hashlib
import json
import os
from collections import defaultdict

from . import __version__

TOKEN_FIELDS = ("input", "output", "cache_read", "cache_creation")
SKIP_MODELS = {"<synthetic>", ""}
MAX_LINE_BYTES = 64 * 1024 * 1024  # skip pathological lines rather than exhaust memory


def _digest(s):
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8", "replace"), digest_size=8).digest(), "big")


def _zero():
    return {k: 0 for k in TOKEN_FIELDS}


def _local_day(ts, use_utc=False, _cache={}):
    """ISO-8601 timestamp -> YYYY-MM-DD in local time (or UTC)."""
    if not ts or not isinstance(ts, str):
        return None
    key = (ts[:16], use_utc)  # minute resolution is enough for a day bucket
    hit = _cache.get(key)
    if hit:
        return hit
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    day = (dt.astimezone(datetime.timezone.utc) if use_utc else dt.astimezone()).strftime("%Y-%m-%d")
    if len(_cache) > 200000:
        _cache.clear()
    _cache[key] = day
    return day


def iter_transcripts(roots):
    for root in roots:
        if os.path.isfile(root) and root.endswith(".jsonl"):
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                if fn.endswith(".jsonl"):
                    yield os.path.join(dirpath, fn)


def _is_prompt(rec):
    """A user record that a person typed (not a tool result or injected meta)."""
    if rec.get("isMeta") or rec.get("isSidechain"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "text" in kinds and "tool_result" not in kinds
    return False


class Scanner:
    def __init__(self, use_utc=False):
        self.use_utc = use_utc
        self.seen_assistant = set()
        self.seen_user = set()
        self.days = defaultdict(lambda: dict(_zero(), messages=0))
        self.day_sessions = defaultdict(set)
        self.models = defaultdict(lambda: dict(_zero(), messages=0))
        self.projects = defaultdict(lambda: {"tokens": 0, "messages": 0, "sessions": set(), "last_day": ""})
        self.sessions = set()
        self.assistant_messages = 0
        self.user_messages = 0
        self.prompts = 0
        self.files = 0
        self.bad_lines = 0
        self.duplicates = 0

    def add_file(self, path):
        self.files += 1
        try:
            fh = open(path, "rb")
        except OSError:
            return
        with fh:
            for raw in fh:
                if len(raw) > MAX_LINE_BYTES:
                    self.bad_lines += 1
                    continue
                # cheap pre-filter: only user/assistant records matter
                if b'"assistant"' not in raw and b'"user"' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    self.bad_lines += 1
                    continue
                if isinstance(rec, dict):
                    self.add_record(rec)

    def add_record(self, rec):
        typ = rec.get("type")
        if typ not in ("assistant", "user"):
            return
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        day = _local_day(rec.get("timestamp"), self.use_utc)
        sid = rec.get("sessionId") or ""
        cwd = rec.get("cwd") or "(unknown)"

        if typ == "assistant":
            key = msg.get("id") or rec.get("requestId") or rec.get("uuid")
            if not key:
                return
            dk = _digest("a:" + key)
            if dk in self.seen_assistant:
                self.duplicates += 1
                return
            self.seen_assistant.add(dk)
            model = msg.get("model") or ""
            if model in SKIP_MODELS:  # locally generated placeholder, no API call
                return
            u = msg.get("usage") or {}
            t = {
                "input": int(u.get("input_tokens") or 0),
                "output": int(u.get("output_tokens") or 0),
                "cache_read": int(u.get("cache_read_input_tokens") or 0),
                "cache_creation": int(u.get("cache_creation_input_tokens") or 0),
            }
            total = sum(t.values())
            self.assistant_messages += 1
            if day:
                d = self.days[day]
                for k in TOKEN_FIELDS:
                    d[k] += t[k]
                d["messages"] += 1
            m = self.models[model]
            for k in TOKEN_FIELDS:
                m[k] += t[k]
            m["messages"] += 1
            p = self.projects[cwd]
            p["tokens"] += total
            p["messages"] += 1
        else:
            key = rec.get("uuid")
            if not key:
                return
            dk = _digest("u:" + key)
            if dk in self.seen_user:
                self.duplicates += 1
                return
            self.seen_user.add(dk)
            self.user_messages += 1
            if _is_prompt(rec):
                self.prompts += 1
            if day:
                self.days[day]["messages"] += 1
            self.projects[cwd]["messages"] += 1

        if sid:
            self.sessions.add(sid)
            self.projects[cwd]["sessions"].add(sid)
            if day:
                self.day_sessions[day].add(sid)
        if day and day > self.projects[cwd]["last_day"]:
            self.projects[cwd]["last_day"] = day

    def result(self):
        days = {}
        for day in sorted(self.days):
            d = dict(self.days[day])
            d["tokens"] = sum(d[k] for k in TOKEN_FIELDS)
            d["sessions"] = len(self.day_sessions.get(day, ()))
            days[day] = d
        models = {}
        for name, m in sorted(self.models.items(), key=lambda kv: -sum(kv[1][k] for k in TOKEN_FIELDS)):
            mm = dict(m)
            mm["tokens"] = sum(m[k] for k in TOKEN_FIELDS)
            models[name] = mm
        projects = {}
        for cwd, p in sorted(self.projects.items(), key=lambda kv: -kv[1]["tokens"]):
            projects[cwd] = {"tokens": p["tokens"], "messages": p["messages"],
                             "sessions": len(p["sessions"]), "last_day": p["last_day"]}
        totals = _zero()
        for d in days.values():
            for k in TOKEN_FIELDS:
                totals[k] += d[k]
        totals["tokens"] = sum(totals[k] for k in TOKEN_FIELDS)
        active = [d for d, v in days.items() if v["tokens"] or v["messages"]]
        totals.update({
            "sessions": len(self.sessions),
            "messages": self.assistant_messages + self.user_messages,
            "assistant_messages": self.assistant_messages,
            "user_messages": self.user_messages,
            "prompts": self.prompts,
            "days_active": len(active),
            "first_day": active[0] if active else None,
            "last_day": active[-1] if active else None,
        })
        return {
            "schema": 1,
            "generator": "mindledger %s" % __version__,
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "timezone": "UTC" if self.use_utc else "local",
            "totals": totals,
            "days": days,
            "models": models,
            "projects": projects,
            "scan": {"files": self.files, "bad_lines": self.bad_lines, "duplicates_skipped": self.duplicates},
        }


def scan(roots, use_utc=False):
    s = Scanner(use_utc=use_utc)
    for path in iter_transcripts(roots):
        s.add_file(path)
    return s.result()


def write_json(obj, path):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=False)
    os.replace(tmp, path)


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

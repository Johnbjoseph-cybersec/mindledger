"""Redact credentials from text before it is written into a backup.

Values are replaced with a REDACTED marker; names and surrounding structure
are kept so the file stays readable and JSON stays valid. Reports counts only,
never the value.
"""
import json
import re

REDACTED = "REDACTED"

PATTERNS = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "sk-ant-" + REDACTED),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), "sk-" + REDACTED),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AKIA_" + REDACTED),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "gh_" + REDACTED),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github_pat_" + REDACTED),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "xox-" + REDACTED),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"), "AIza_" + REDACTED),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "JWT_" + REDACTED),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "-----" + REDACTED + " PRIVATE KEY-----"),
    ("bearer", re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9_\-\.=]{16,}"), r"\1" + REDACTED),
    ("assignment", re.compile(
        r"(?i)((?:api[_-]?key|secret(?:[_-]?key)?|client[_-]?secret|password|passwd|access[_-]?token|"
        r"auth[_-]?token|refresh[_-]?token|token)[\"']?\s*[=:]\s*[\"']?)([A-Za-z0-9_\-\.+/]{12,})"),
     r"\1" + REDACTED),
]

SENSITIVE_KEY = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|credential|auth[_-]?(?:token|key|header)|private[_-]?key)")


def scrub_text(text):
    counts = {}
    for name, pat, repl in PATTERNS:
        text, n = pat.subn(repl, text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts


def _scrub_json_value(obj, counts):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and SENSITIVE_KEY.search(str(k)) and v and v != REDACTED:
                out[k] = REDACTED
                counts["sensitive-key"] = counts.get("sensitive-key", 0) + 1
            else:
                out[k] = _scrub_json_value(v, counts)
        return out
    if isinstance(obj, list):
        return [_scrub_json_value(v, counts) for v in obj]
    if isinstance(obj, str):
        new, c = scrub_text(obj)
        for k, n in c.items():
            counts[k] = counts.get(k, 0) + n
        return new
    return obj


def scrub_json_text(text):
    """For settings-like JSON: redact any string value under a sensitive-looking key
    (e.g. env.SOME_API_TOKEN) plus every pattern match. Falls back to text mode if
    the input is not valid JSON."""
    try:
        obj = json.loads(text)
    except ValueError:
        return scrub_text(text)
    counts = {}
    obj = _scrub_json_value(obj, counts)
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n", counts

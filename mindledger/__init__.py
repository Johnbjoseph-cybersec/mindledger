"""MindLedger: a local, private ledger of your AI coding-agent usage.

Reads the session transcripts Claude Code writes to disk, turns them into
token / session / message statistics, keeps an append-only ledger that refuses
to silently go backwards, backs up your agent configuration, and renders a
self-contained HTML report. Python 3 standard library only. No network calls.
"""

__version__ = "0.1.0"
NAME = "mindledger"
DISPLAY_NAME = "MindLedger"

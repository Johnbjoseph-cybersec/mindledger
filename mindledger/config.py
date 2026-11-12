"""Paths and environment overrides. Nothing here is machine-specific."""
import os

ENV_PREFIX = "MINDLEDGER"


def _env(name, default=None):
    return os.environ.get("%s_%s" % (ENV_PREFIX, name), default)


def data_home():
    """Where MindLedger keeps its own state (stats, ledger, vault, index)."""
    return os.path.abspath(os.path.expanduser(_env("HOME", "~/.mindledger")))


def agent_home():
    """The agent's config directory (Claude Code default: ~/.claude)."""
    return os.path.abspath(os.path.expanduser(_env("CLAUDE_HOME", "~/.claude")))


def project_roots(cli_roots=None):
    """Transcript roots. CLI flags win, then MINDLEDGER_PROJECTS (os.pathsep list),
    then <agent_home>/projects."""
    if cli_roots:
        roots = cli_roots
    else:
        env = _env("PROJECTS")
        roots = env.split(os.pathsep) if env else [os.path.join(agent_home(), "projects")]
    return [os.path.abspath(os.path.expanduser(r)) for r in roots if r]


def path_in_home(*parts):
    return os.path.join(data_home(), *parts)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path

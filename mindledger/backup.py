"""Mirror the agent's durable configuration into a vault, then zip it.

What is backed up (relative to the agent home, default ~/.claude):
    CLAUDE.md, settings.json, settings.local.json, skills/, agents/, commands/,
    and every projects/<dir>/memory/ folder (the agent's auto-memory).
Transcripts are NOT copied - they are large and are summarised by `scan`.

Every text file is passed through the secret scrubber before it lands in the
vault, so a token pasted into settings or a memory note does not travel into
every dated zip. Symlinks are skipped (never followed) and files above the
size cap are skipped and reported.

Layout inside the vault and inside each zip:
    mirror/CLAUDE.md, mirror/settings.json, mirror/skills/..., mirror/agents/...,
    mirror/commands/..., mirror/memory/<project-dir>/...
"""
import datetime
import os
import shutil
import zipfile

from .scrub import scrub_json_text, scrub_text

SINGLE_FILES = ["CLAUDE.md", "settings.json", "settings.local.json"]
TREES = ["skills", "agents", "commands"]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
MAX_FILE_BYTES = 5 * 1024 * 1024
ZIP_PREFIX = "mindledger_"


def plan(agent_home):
    """Yield (source_path, mirror_relpath) for everything that should be backed up."""
    for name in SINGLE_FILES:
        src = os.path.join(agent_home, name)
        if os.path.isfile(src) and not os.path.islink(src):
            yield src, name
    for tree in TREES:
        base = os.path.join(agent_home, tree)
        yield from _walk(base, tree)
    projects = os.path.join(agent_home, "projects")
    if os.path.isdir(projects):
        for proj in sorted(os.listdir(projects)):
            mem = os.path.join(projects, proj, "memory")
            yield from _walk(mem, os.path.join("memory", proj))


def _walk(base, rel_prefix):
    if not os.path.isdir(base) or os.path.islink(base):
        return
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            src = os.path.join(dirpath, fn)
            if os.path.islink(src) or not os.path.isfile(src):
                continue
            yield src, os.path.join(rel_prefix, os.path.relpath(src, base))


def _is_text(data):
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def mirror(agent_home, vault):
    """Copy (and scrub) into <vault>/mirror. Removes files no longer present at the source."""
    dest_root = os.path.join(vault, "mirror")
    os.makedirs(dest_root, exist_ok=True)
    report = {"files": 0, "skipped_large": 0, "redactions": {}, "removed": 0}
    wanted = set()
    for src, rel in plan(agent_home):
        try:
            size = os.path.getsize(src)
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            report["skipped_large"] += 1
            continue
        with open(src, "rb") as fh:
            data = fh.read()
        if _is_text(data):
            text = data.decode("utf-8")
            if rel.endswith(".json"):
                text, counts = scrub_json_text(text)
            else:
                text, counts = scrub_text(text)
            for k, n in counts.items():
                report["redactions"][k] = report["redactions"].get(k, 0) + n
            data = text.encode("utf-8")
        dest = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
        wanted.add(os.path.normpath(rel))
        report["files"] += 1
    for dirpath, _, filenames in os.walk(dest_root, topdown=False):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if os.path.normpath(os.path.relpath(p, dest_root)) not in wanted:
                os.remove(p)
                report["removed"] += 1
        if dirpath != dest_root and not os.listdir(dirpath):
            os.rmdir(dirpath)
    return report


def make_zip(vault, keep=30, now=None):
    """Write a dated zip of <vault>/mirror into <vault>/snapshots and prune to `keep`."""
    snaps = os.path.join(vault, "snapshots")
    os.makedirs(snaps, exist_ok=True)
    stamp = (now or datetime.datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(snaps, "%s%s.zip" % (ZIP_PREFIX, stamp))
    tmp = path + ".tmp"
    root = os.path.join(vault, "mirror")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                p = os.path.join(dirpath, fn)
                zf.write(p, os.path.join("mirror", os.path.relpath(p, root)))
    os.replace(tmp, path)
    pruned = prune(snaps, keep)
    return path, pruned


def list_snapshots(snaps):
    if not os.path.isdir(snaps):
        return []
    return sorted(os.path.join(snaps, f) for f in os.listdir(snaps)
                  if f.startswith(ZIP_PREFIX) and f.endswith(".zip"))


def prune(snaps, keep):
    keep = max(1, int(keep))
    old = list_snapshots(snaps)[:-keep]
    for p in old:
        os.remove(p)
    return old


def _target_for(member, agent_home):
    """Map a zip member back to its live location. Returns None for anything unsafe."""
    norm = member.replace("\\", "/")
    if norm.endswith("/") or norm.startswith("/") or ".." in norm.split("/"):
        return None
    parts = norm.split("/")
    if parts[0] != "mirror" or len(parts) < 2:
        return None
    rel = parts[1:]
    if rel[0] == "memory":
        if len(rel) < 3:
            return None
        target = os.path.join(agent_home, "projects", rel[1], "memory", *rel[2:])
    else:
        target = os.path.join(agent_home, *rel)
    real_home = os.path.realpath(agent_home)
    if not os.path.realpath(target).startswith(real_home + os.sep):
        return None
    return target


def restore(zip_path, agent_home, dry_run=True, now=None):
    """Restore a snapshot. Existing files are kept as <file>.bak-<stamp> first.
    Returns a list of (action, member, target)."""
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d%H%M%S")
    actions = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = _target_for(info.filename, agent_home)
            if target is None:
                actions.append(("REJECT", info.filename, None))
                continue
            action = "OVERWRITE" if os.path.exists(target) else "CREATE"
            actions.append((action, info.filename, target))
            if dry_run:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.exists(target):
                shutil.copy2(target, "%s.bak-%s" % (target, stamp))
            with zf.open(info) as src, open(target + ".tmp", "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(target + ".tmp", target)
    return actions

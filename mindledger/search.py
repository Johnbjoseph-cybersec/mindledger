"""Local full-text search over session text.

Builds an incremental SQLite index (unchanged files are skipped by mtime+size).
Uses FTS5 with BM25 ranking when the local SQLite has it, otherwise falls back
to a plain substring scan over the same table. Only human-readable text is
indexed: typed prompts and assistant text blocks, not tool payloads.
"""
import json
import os
import re
import sqlite3

from .scan import _local_day, iter_transcripts

CHUNK_CHARS = 1500
MSG_CAP = 4000


def connect(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER);
    CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, path TEXT, session TEXT,
        day TEXT, cwd TEXT, text TEXT);
    CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
    """)
    fts = True
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id')")
    except sqlite3.OperationalError:
        fts = False
    return con, fts


def _texts(rec):
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                yield b["text"]


def _chunks_of(path):
    session, cwd, day, buf = "", "", "", []
    size = 0
    seen = set()
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            if '"assistant"' not in line and '"user"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("type") not in ("user", "assistant"):
                continue
            if rec.get("isMeta"):
                continue
            key = rec.get("uuid")
            if key in seen:
                continue
            seen.add(key)
            session = rec.get("sessionId") or session
            cwd = rec.get("cwd") or cwd
            day = _local_day(rec.get("timestamp")) or day
            for t in _texts(rec):
                t = t.strip()
                if not t:
                    continue
                piece = "%s: %s" % (rec["type"], t[:MSG_CAP])
                buf.append(piece)
                size += len(piece)
                if size >= CHUNK_CHARS:
                    yield session, day, cwd, "\n".join(buf)
                    buf, size = [], 0
    if buf:
        yield session, day, cwd, "\n".join(buf)


def build_index(db_path, roots):
    con, fts = connect(db_path)
    added = 0
    live = set()
    for path in iter_transcripts(roots):
        live.add(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        row = con.execute("SELECT mtime,size FROM files WHERE path=?", (path,)).fetchone()
        if row and abs(row[0] - st.st_mtime) < 1e-6 and row[1] == st.st_size:
            continue
        _drop(con, fts, path)
        for session, day, cwd, text in _chunks_of(path):
            cur = con.execute("INSERT INTO chunks(path,session,day,cwd,text) VALUES(?,?,?,?,?)",
                              (path, session, day, cwd, text))
            if fts:
                con.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cur.lastrowid, text))
            added += 1
        con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (path, st.st_mtime, st.st_size))
    for (path,) in con.execute("SELECT path FROM files").fetchall():
        if path not in live:
            _drop(con, fts, path)
            con.execute("DELETE FROM files WHERE path=?", (path,))
    con.commit()
    con.close()
    return added, fts


def _drop(con, fts, path):
    if fts:
        rows = con.execute("SELECT id,text FROM chunks WHERE path=?", (path,)).fetchall()
        con.executemany("INSERT INTO chunks_fts(chunks_fts,rowid,text) VALUES('delete',?,?)", rows)
    con.execute("DELETE FROM chunks WHERE path=?", (path,))


def _fts_query(q):
    """Turn free text into a safe FTS5 query: every word quoted, AND-ed together."""
    words = re.findall(r"[\w\-\.]+", q, flags=re.UNICODE)
    return " ".join('"%s"' % w.replace('"', "") for w in words)


def search(db_path, query, limit=10, force_fallback=False):
    con, fts = connect(db_path)
    rows = []
    q = _fts_query(query)
    if fts and not force_fallback and q:
        try:
            rows = con.execute(
                "SELECT c.day, c.cwd, c.session, snippet(chunks_fts, 0, '[', ']', ' ... ', 18) "
                "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?", (q, limit)).fetchall()
            con.close()
            return rows, "fts5"
        except sqlite3.OperationalError:
            pass
    words = [w.lower() for w in re.findall(r"[\w\-\.]+", query)]
    for day, cwd, session, text in con.execute("SELECT day,cwd,session,text FROM chunks ORDER BY day DESC"):
        low = text.lower()
        if words and all(w in low for w in words):
            i = low.find(words[0])
            rows.append((day, cwd, session, " ".join(text[max(0, i - 80): i + 160].split())))
            if len(rows) >= limit:
                break
    con.close()
    return rows, "scan"

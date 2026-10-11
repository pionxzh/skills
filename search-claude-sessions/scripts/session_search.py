#!/usr/bin/env python3
"""Full-text search over local Claude Code session transcripts.

Index user prompts, assistant text, and tool-call summaries from session JSONL
files into a SQLite FTS5 (trigram) database, then search, list recent turns,
expand a turn, or show the raw tool call and result behind a hit.

The index is incremental: each run reads only bytes appended since the last
run, and `search`, `recent`, and `context` refresh the selected scope first.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA_VERSION = 1
MAX_PROJECT_KEY_LENGTH = 200
TOOL_SUMMARY_LIMIT = 1500
SNIPPET_RADIUS = 150

SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
CHANNEL_RE = re.compile(r"<channel\s+([^>]*)>(.*?)</channel>", re.DOTALL)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
REF_RE = re.compile(r"^([A-Za-z0-9-]+):(\d+)$")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    inode       INTEGER NOT NULL,
    byte_offset INTEGER NOT NULL,
    line_no     INTEGER NOT NULL,
    prompt_id   TEXT,
    title       TEXT,
    cwd         TEXT,
    first_ts    TEXT,
    last_ts     TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL,
    project     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    line_no     INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    role        TEXT NOT NULL,
    prompt_id   TEXT,
    tool_name   TEXT,
    meta        TEXT,
    content     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_path_line ON messages(path, line_no);
CREATE INDEX IF NOT EXISTS idx_messages_prompt ON messages(prompt_id);
CREATE INDEX IF NOT EXISTS idx_messages_project_ts ON messages(project, ts);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, meta, tool_name,
    content='messages', content_rowid='id', tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, meta, tool_name)
    VALUES (new.id, new.content, new.meta, new.tool_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, meta, tool_name)
    VALUES ('delete', old.id, old.content, old.meta, old.tool_name);
END;
"""


# --------------------------------------------------------------------------
# Locating transcripts (same resolution rules as read-claude-session)
# --------------------------------------------------------------------------


def fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def claude_config_dir(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


def _javascript_string_hash(value: str) -> int:
    """Reproduce Claude Code's signed 32-bit JavaScript string hash."""
    hash_value = 0
    utf16 = value.encode("utf-16-le", errors="surrogatepass")
    for index in range(0, len(utf16), 2):
        code_unit = int.from_bytes(utf16[index : index + 2], "little")
        hash_value = ((hash_value << 5) - hash_value + code_unit) & 0xFFFFFFFF
    if hash_value >= 0x80000000:
        hash_value -= 0x100000000
    return hash_value


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = []
    while value:
        value, remainder = divmod(value, 36)
        result.append(digits[remainder])
    return "".join(reversed(result))


def project_key(project_path: Path | str) -> str:
    """Encode a project path the same way as current Claude Code."""
    value = str(project_path)
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", value)
    if len(sanitized) <= MAX_PROJECT_KEY_LENGTH:
        return sanitized
    suffix = _base36(abs(_javascript_string_hash(value)))
    return f"{sanitized[:MAX_PROJECT_KEY_LENGTH]}-{suffix}"


def projects_root(config_dir: Path) -> Path:
    root = config_dir / "projects"
    if not root.is_dir():
        fail(f"Claude projects directory not found: {root}")
    return root


def candidate_project_dirs(root: Path, project: Path) -> list[Path]:
    expanded = project.expanduser()
    paths: list[Path] = []
    for value in (expanded.absolute(), expanded.resolve()):
        candidate = root / project_key(value)
        if candidate.is_dir() and candidate not in paths:
            paths.append(candidate)
    if paths:
        return paths
    # Legacy releases and moved/symlinked projects may carry another suffix.
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", str(expanded.absolute()))
    prefix = sanitized[:MAX_PROJECT_KEY_LENGTH]
    for candidate in sorted(root.glob(f"{prefix}*")):
        if candidate.is_dir() and candidate not in paths:
            paths.append(candidate)
    return paths


def all_project_dirs(root: Path) -> list[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir())


def resolve_scope(args: argparse.Namespace) -> tuple[Path, list[Path]]:
    root = projects_root(claude_config_dir(args.config_dir))
    if args.project_dir:
        dirs = [Path(args.project_dir).expanduser()]
    elif args.all_projects:
        dirs = all_project_dirs(root)
    else:
        project = Path(args.project).expanduser() if args.project else Path.cwd()
        dirs = candidate_project_dirs(root, project)
        if not dirs:
            fail(
                f"No Claude transcripts for {project.absolute()}. "
                "Use --all-projects or --project PATH."
            )
    return root, dirs


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------


def default_db_path() -> Path:
    configured = os.environ.get("CLAUDE_SESSION_SEARCH_DB")
    if configured:
        return Path(configured).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache).expanduser() / "claude-session-search" / "index.db"


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        conn.executescript(
            """
            DROP TABLE IF EXISTS messages_fts;
            DROP TABLE IF EXISTS messages;
            DROP TABLE IF EXISTS files;
            """
        )
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.executescript(SCHEMA_SQL)
    return conn


# --------------------------------------------------------------------------
# Parsing transcript records
# --------------------------------------------------------------------------


def clean_text(text: str) -> str:
    return SYSTEM_REMINDER_RE.sub("", text).strip()


def split_channel(text: str) -> tuple[str, str | None]:
    """Return (inner text, attribute string) for a <channel ...> message."""
    match = CHANNEL_RE.search(text)
    if not match:
        return text, None
    attrs = " ".join(f"{k}={v}" for k, v in ATTR_RE.findall(match.group(1)))
    return match.group(2).strip(), attrs or None


def user_text(content: object) -> str:
    if isinstance(content, str):
        return clean_text(content)
    if isinstance(content, list):
        parts = [
            clean_text(str(block.get("text", "")))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n\n".join(part for part in parts if part)
    return ""


def summarize_tool(name: str, input_: object) -> str:
    if not isinstance(input_, dict) or not input_:
        return name
    if name == "Bash":
        description = str(input_.get("description", "")).strip()
        command = str(input_.get("command", ""))[:TOOL_SUMMARY_LIMIT]
        return f"{description}\n$ {command}" if description else f"$ {command}"
    for key in ("file_path", "notebook_path"):
        if key in input_:
            return str(input_[key])
    if name in {"Agent", "Task"}:
        description = input_.get("description", "")
        prompt = str(input_.get("prompt", ""))[:TOOL_SUMMARY_LIMIT]
        return f"{description}\n{prompt}".strip()
    text = json.dumps(input_, ensure_ascii=False)
    return text[:TOOL_SUMMARY_LIMIT]


def parse_record(obj: dict) -> Iterator[tuple[str, str | None, str | None, str]]:
    """Yield (role, tool_name, meta, content) rows for one transcript record."""
    kind = obj.get("type")
    message = obj.get("message") or {}
    content = message.get("content")
    if kind == "user" and not obj.get("isMeta"):
        text = user_text(content)
        if text:
            text, meta = split_channel(text)
            if text:
                yield "user", None, meta, text
    elif kind == "assistant" and isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = clean_text(str(block.get("text", "")))
                if text:
                    yield "assistant", None, None, text
            elif block.get("type") == "tool_use":
                name = str(block.get("name", ""))
                yield "tool", name, None, summarize_tool(name, block.get("input"))


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------


def transcript_files(dirs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for directory in dirs:
        files.extend(sorted(directory.glob("*.jsonl")))
    return files


def index_file(conn: sqlite3.Connection, path: Path) -> int:
    """Index bytes appended to one transcript since the last run."""
    stat = path.stat()
    key = str(path)
    state = conn.execute("SELECT * FROM files WHERE path = ?", (key,)).fetchone()
    if state and (state["inode"] != stat.st_ino or stat.st_size < state["byte_offset"]):
        conn.execute("DELETE FROM messages WHERE path = ?", (key,))
        conn.execute("DELETE FROM files WHERE path = ?", (key,))
        state = None
    if state and state["byte_offset"] == stat.st_size:
        return 0

    offset = state["byte_offset"] if state else 0
    line_no = state["line_no"] if state else 0
    prompt_id = state["prompt_id"] if state else None
    title = state["title"] if state else None
    cwd = state["cwd"] if state else None
    first_ts = state["first_ts"] if state else None
    last_ts = state["last_ts"] if state else None
    project = path.parent.name
    session_id = path.stem
    rows = []

    with path.open("rb") as handle:
        handle.seek(offset)
        for raw in handle:
            # A line without its newline is still being written; stop there and
            # pick it up on the next run.
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            line_no += 1
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            if kind == "ai-title" and obj.get("aiTitle"):
                title = obj["aiTitle"]
                continue
            if kind == "summary" and obj.get("summary") and not title:
                title = obj["summary"]
                continue
            if obj.get("promptId"):
                prompt_id = obj["promptId"]
            cwd = obj.get("cwd") or cwd
            ts = obj.get("timestamp") or ""
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            for role, tool_name, meta, content in parse_record(obj):
                rows.append(
                    (key, project, session_id, line_no, ts, role, prompt_id,
                     tool_name, meta, content)
                )

    conn.executemany(
        """INSERT INTO messages
           (path, project, session_id, line_no, ts, role, prompt_id,
            tool_name, meta, content)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.execute(
        """INSERT OR REPLACE INTO files
           (path, project, session_id, inode, byte_offset, line_no, prompt_id,
            title, cwd, first_ts, last_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (key, project, session_id, stat.st_ino, offset, line_no, prompt_id,
         title, cwd, first_ts, last_ts),
    )
    return len(rows)


def refresh(conn: sqlite3.Connection, dirs: list[Path], verbose: bool = False) -> int:
    # Rows of transcripts that no longer exist are kept on purpose: Claude
    # Code deletes JSONL files after cleanupPeriodDays (30 by default), and
    # the index is then the only copy of those sessions.
    files = transcript_files(dirs)
    total = 0
    for path in files:
        try:
            added = index_file(conn, path)
            conn.commit()
        except OSError as error:
            conn.rollback()
            print(f"  skip {path.name}: {error}", file=sys.stderr)
            continue
        if added and verbose:
            print(f"  {path.stem[:8]}  +{added}", file=sys.stderr)
        total += added
    return total


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def local_time(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def utc_bound(value: str) -> str:
    """Convert a local date or datetime to the UTC string format in the index."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        fail(f"Invalid date {value!r}; use YYYY-MM-DD or YYYY-MM-DDTHH:MM")
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def ref_of(row: sqlite3.Row) -> str:
    return f"{row['session_id'][:8]}:{row['line_no']}"


def label_of(row: sqlite3.Row) -> str:
    if row["role"] == "tool":
        return f"tool:{row['tool_name']}"
    return row["role"]


def one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def snippet(text: str, terms: list[str], full: bool) -> str:
    """Cut a window around the first matched term and mark every match."""
    if not full and terms:
        lowered = text.lower()
        hits = [lowered.find(term.lower()) for term in terms]
        hits = [hit for hit in hits if hit >= 0]
        if hits:
            start = max(0, min(hits) - SNIPPET_RADIUS)
            end = min(len(text), min(hits) + SNIPPET_RADIUS * 2)
            text = ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
        elif len(text) > SNIPPET_RADIUS * 2:
            text = text[: SNIPPET_RADIUS * 2] + "…"
    elif not full and len(text) > SNIPPET_RADIUS * 2:
        text = text[: SNIPPET_RADIUS * 2] + "…"
    for term in sorted(set(terms), key=len, reverse=True):
        text = re.sub(re.escape(term), lambda m: f">>>{m.group(0)}<<<", text, flags=re.IGNORECASE)
    return text


def find_file(conn: sqlite3.Connection, prefix: str) -> sqlite3.Row:
    rows = conn.execute(
        "SELECT * FROM files WHERE session_id LIKE ? ORDER BY last_ts DESC",
        (prefix + "%",),
    ).fetchall()
    exact = [row for row in rows if row["session_id"] == prefix]
    if exact:
        rows = exact
    if not rows:
        fail(f"No indexed session matches {prefix!r}. Run 'index --all-projects' if it is in another project.")
    if len(rows) > 1:
        listing = "\n".join(f"  {r['session_id']}  {r['title'] or ''}" for r in rows[:20])
        fail(f"Ambiguous session prefix {prefix!r}:\n{listing}")
    return rows[0]


def parse_ref(value: str) -> tuple[str, int]:
    match = REF_RE.match(value)
    if not match:
        fail(f"Expected a reference like 1a2b3c4d:120, got {value!r}")
    return match.group(1), int(match.group(2))


def project_filter(dirs: list[Path], all_projects: bool) -> tuple[str, list[str]]:
    if all_projects:
        return "", []
    names = [directory.name for directory in dirs]
    return f"m.project IN ({','.join('?' * len(names))})", names


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def prepare(args: argparse.Namespace) -> tuple[sqlite3.Connection, list[Path]]:
    _, dirs = resolve_scope(args)
    conn = open_db(Path(args.db).expanduser() if args.db else default_db_path())
    if not getattr(args, "no_index", False):
        refresh(conn, dirs)
    return conn, dirs


def cmd_index(args: argparse.Namespace) -> None:
    _, dirs = resolve_scope(args)
    db_path = Path(args.db).expanduser() if args.db else default_db_path()
    conn = open_db(db_path)
    if args.rebuild:
        # Re-read transcripts that still exist; keep rows of deleted ones.
        for path in transcript_files(dirs):
            conn.execute("DELETE FROM messages WHERE path = ?", (str(path),))
            conn.execute("DELETE FROM files WHERE path = ?", (str(path),))
        conn.commit()
    if not args.stats:
        added = refresh(conn, dirs, verbose=True)
        print(f"+{added} rows indexed", file=sys.stderr)
    print(f"DB: {db_path}")
    for row in conn.execute(
        """SELECT f.project, COUNT(DISTINCT f.path) AS sessions,
                  (SELECT COUNT(*) FROM messages m WHERE m.project = f.project) AS rows,
                  MAX(f.last_ts) AS last_ts
           FROM files f GROUP BY f.project ORDER BY last_ts DESC"""
    ):
        print(
            f"  {row['sessions']:4d} sessions  {row['rows']:7d} rows  "
            f"last {local_time(row['last_ts'])}  {row['project']}"
        )


def query_terms(raw: str) -> list[str]:
    try:
        terms = shlex.split(raw)
    except ValueError:
        terms = raw.split()
    return [term for term in terms if term.strip()]


def cmd_search(args: argparse.Namespace) -> None:
    conn, dirs = prepare(args)
    conditions: list[str] = []
    params: list[object] = []
    terms = query_terms(args.query) if args.query and not args.fts else []

    scope_sql, scope_params = project_filter(dirs, args.all_projects)
    if scope_sql:
        conditions.append(scope_sql)
        params.extend(scope_params)

    fts_query = None
    if args.fts:
        fts_query = args.query
    elif terms:
        # The trigram tokenizer needs at least three characters per term.
        # Shorter terms (common in CJK, such as two-character words) fall back
        # to a substring scan.
        long_terms = [t for t in terms if len(t) >= 3]
        for term in terms:
            if len(term) < 3:
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                conditions.append(
                    "(m.content LIKE ? ESCAPE '\\' OR m.meta LIKE ? ESCAPE '\\' "
                    "OR m.tool_name LIKE ? ESCAPE '\\')"
                )
                params.extend([f"%{escaped}%"] * 3)
        if long_terms:
            fts_query = " AND ".join('"' + t.replace('"', '""') + '"' for t in long_terms)

    if args.role:
        conditions.append("m.role = ?")
        params.append(args.role)
    if args.tool:
        conditions.append("m.tool_name LIKE ?")
        params.append(f"%{args.tool}%")
    if args.session:
        conditions.append("m.session_id LIKE ?")
        params.append(args.session + "%")
    if args.after:
        conditions.append("m.ts >= ?")
        params.append(utc_bound(args.after))
    if args.before:
        conditions.append("m.ts < ?")
        params.append(utc_bound(args.before))
    if not (fts_query or terms or args.role or args.tool or args.session or args.after or args.before):
        fail("Provide a query or at least one filter")

    if fts_query:
        source = "messages_fts JOIN messages m ON m.id = messages_fts.rowid"
        conditions.insert(0, "messages_fts MATCH ?")
        params.insert(0, fts_query)
        order = "messages_fts.rank" if args.sort == "rank" else "m.ts DESC"
    else:
        source = "messages m"
        order = "m.ts DESC"

    where = " AND ".join(conditions) or "1"
    sql = f"""SELECT m.*, f.title FROM {source}
              LEFT JOIN files f ON f.path = m.path
              WHERE {where} ORDER BY {order} LIMIT ?"""
    params.append(args.limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as error:
        fail(f"Query failed: {error}")

    if args.json:
        print(json.dumps(
            [{**dict(row), "ref": ref_of(row)} for row in rows],
            ensure_ascii=False, indent=2,
        ))
        return
    if not rows:
        print("No results.")
        return

    for row in rows:
        title = row["title"] or row["session_id"][:8]
        meta = f"  [{row['meta']}]" if row["meta"] else ""
        print(f"[{local_time(row['ts'])}] {ref_of(row)}  {label_of(row)}  \"{title}\"{meta}")
        if args.all_projects:
            print(f"  project: {row['project']}")
        for line in snippet(row["content"], terms, args.full).splitlines():
            print(f"  > {line}")
        print("---")
    print(f"{len(rows)} results (limit {args.limit}). Expand with: context <ref> / detail <ref>")


def cmd_recent(args: argparse.Namespace) -> None:
    conn, dirs = prepare(args)
    scope_sql, params = project_filter(dirs, args.all_projects)
    conditions = ["m.prompt_id IS NOT NULL"]
    if scope_sql:
        conditions.append(scope_sql)
    if args.session:
        conditions.append("m.session_id LIKE ?")
        params.append(args.session + "%")
    turns = conn.execute(
        f"""SELECT m.prompt_id, MAX(m.ts) AS last_ts FROM messages m
            WHERE {' AND '.join(conditions)}
            GROUP BY m.prompt_id ORDER BY last_ts DESC LIMIT ?""",
        [*params, args.count],
    ).fetchall()
    if not turns:
        print("No turns found.")
        return

    width = 500 if args.full else 200
    for turn in reversed(turns):
        rows = conn.execute(
            """SELECT m.*, f.title FROM messages m LEFT JOIN files f ON f.path = m.path
               WHERE m.prompt_id = ? ORDER BY m.line_no""",
            (turn["prompt_id"],),
        ).fetchall()
        user = next((row for row in rows if row["role"] == "user"), None)
        answer = " ".join(row["content"] for row in rows if row["role"] == "assistant")
        tools = [row["tool_name"] for row in rows if row["role"] == "tool"]
        first = user or rows[0]
        print(f"[{local_time(turn['last_ts'])}] {ref_of(first)}  \"{first['title'] or ''}\"")
        if user:
            meta = f" [{user['meta']}]" if user["meta"] else ""
            print(f"  Q{meta}: {one_line(user['content'], width)}")
        if answer:
            print(f"  A: {one_line(answer, width)}")
        if tools:
            names = ", ".join(dict.fromkeys(tools))
            print(f"  tools ({len(tools)}): {one_line(names, 160)}")
        print("---")


def cmd_context(args: argparse.Namespace) -> None:
    conn, _ = prepare(args)
    prefix, line = parse_ref(args.ref)
    file_row = find_file(conn, prefix)
    if args.radius is not None:
        rows = conn.execute(
            """SELECT * FROM messages WHERE path = ? AND line_no BETWEEN ? AND ?
               ORDER BY line_no""",
            (file_row["path"], line - args.radius, line + args.radius),
        ).fetchall()
    else:
        anchor = conn.execute(
            """SELECT prompt_id FROM messages WHERE path = ? AND line_no <= ?
               ORDER BY line_no DESC LIMIT 1""",
            (file_row["path"], line),
        ).fetchone()
        if not anchor or not anchor["prompt_id"]:
            fail("No turn found at that line; try --radius 40")
        rows = conn.execute(
            "SELECT * FROM messages WHERE path = ? AND prompt_id = ? ORDER BY line_no",
            (file_row["path"], anchor["prompt_id"]),
        ).fetchall()

    print(f"Session {file_row['session_id']}  \"{file_row['title'] or ''}\"")
    print(f"File {file_row['path']}")
    print("=" * 60)
    for row in rows:
        meta = f" [{row['meta']}]" if row["meta"] else ""
        print(f"\n## {label_of(row)}  {ref_of(row)}  {local_time(row['ts'])}{meta}")
        content = row["content"]
        if row["role"] == "tool" and not args.full:
            content = one_line(content, 200)
        print(content)


def block_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def cmd_detail(args: argparse.Namespace) -> None:
    conn = open_db(Path(args.db).expanduser() if args.db else default_db_path())
    prefix, target = parse_ref(args.ref)
    path = Path(find_file(conn, prefix)["path"])
    limit = args.max_chars or None

    def clip(text: str) -> str:
        if limit and len(text) > limit:
            return text[:limit] + f"\n… truncated ({len(text)} chars total; --max-chars 0 for all)"
        return text

    pending: dict[str, str] = {}
    with path.open("rb") as handle:
        for number, raw in enumerate(handle, start=1):
            if number < target:
                continue
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            content = (obj.get("message") or {}).get("content")
            if number == target:
                if isinstance(content, str):
                    print(clip(clean_text(content)))
                    return
                for block in content or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        print(clip(clean_text(str(block.get("text", "")))))
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "")
                        input_ = block.get("input") or {}
                        print(f"=== tool_use {name} (line {number}) ===")
                        if name == "Bash" and isinstance(input_, dict):
                            if input_.get("description"):
                                print(f"# {input_['description']}")
                            print(f"$ {input_.get('command', '')}")
                        else:
                            print(json.dumps(input_, ensure_ascii=False, indent=2))
                        print()
                        pending[block.get("id", "")] = name
                if not pending:
                    return
                continue
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    name = pending.pop(block.get("tool_use_id", ""), None)
                    if name is None:
                        continue
                    status = "error" if block.get("is_error") else "result"
                    print(f"=== tool_result {name} {status} (line {number}) ===")
                    print(clip(block_text(block.get("content"))))
                    print()
            if not pending or number > target + 5000:
                break
    for name in pending.values():
        print(f"(no tool_result found for {name})")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    scope = argparse.ArgumentParser(add_help=False)
    scope.add_argument("--project", help="Source project path (default: current directory)")
    scope.add_argument("--project-dir", help="Exact Claude project transcript directory")
    scope.add_argument("--all-projects", action="store_true", help="Every Claude project")
    scope.add_argument("--config-dir", help="Override CLAUDE_CONFIG_DIR")
    scope.add_argument("--db", help="Index database path (default: ~/.cache/claude-session-search/index.db)")
    scope.add_argument("--no-index", action="store_true", help="Skip the incremental refresh")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", parents=[scope], help="Refresh the index and show stats")
    p.add_argument("--rebuild", action="store_true", help="Drop everything and re-index the scope")
    p.add_argument("--stats", action="store_true", help="Show stats without refreshing")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", parents=[scope], help="Search messages")
    p.add_argument("query", nargs="?", help="Terms (all must match); quote a phrase")
    p.add_argument("--fts", action="store_true", help="Pass the query to FTS5 as-is (OR, NEAR, ...)")
    p.add_argument("--role", choices=["user", "assistant", "tool"])
    p.add_argument("--tool", help="Tool name substring, e.g. Bash or mcp__")
    p.add_argument("--session", help="Session ID prefix")
    p.add_argument("--after", help="Local date/time, inclusive")
    p.add_argument("--before", help="Local date/time, exclusive")
    p.add_argument("--sort", choices=["time", "rank"], default="time")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--full", action="store_true", help="Print whole messages")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("recent", parents=[scope], help="Show the latest turns")
    p.add_argument("count", nargs="?", type=int, default=5)
    p.add_argument("--session", help="Session ID prefix")
    p.add_argument("--full", action="store_true", help="Longer excerpts")
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser("context", parents=[scope], help="Show the turn around a ref")
    p.add_argument("ref", help="<session-prefix>:<line> from search output")
    p.add_argument("--radius", type=int, help="Show +/- N transcript lines instead of the turn")
    p.add_argument("--full", action="store_true", help="Do not shorten tool calls")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("detail", parents=[scope], help="Raw tool call and result at a ref")
    p.add_argument("ref", help="<session-prefix>:<line> of a tool row")
    p.add_argument("--max-chars", type=int, default=4000, help="0 = no limit")
    p.set_defaults(func=cmd_detail)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

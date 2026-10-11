---
name: search-claude-sessions
description: Full-text search across local Claude Code session transcripts to recall past conversations, decisions, commands, SQL, scripts, and tool calls. Use when the user asks what was discussed or decided before, how something was done last time, which session touched a file, or to find a previous command or query. Also use proactively before rebuilding something (a query, script, config, analysis) that a past session may already have produced.
---

# Search Claude Sessions

Search an incremental SQLite FTS5 index of Claude Code transcripts. It indexes user prompts, assistant text, and one summary per tool call. To read one whole session instead, use `read-claude-session`.

## Search

```bash
python3 <skill-dir>/scripts/session_search.py search "<terms>"
```

Every command except `detail` refreshes the index for its scope first. Only bytes appended since the last run are read, so the refresh takes well under a second. The first run over a large history takes about 10 seconds per GB of JSONL.

Query rules:

- Space-separated terms must all match. Matching is case-insensitive substring matching, so `push` also matches `unpushed`.
- Quote a phrase inside the query: `search '"git push" origin'`.
- Terms of one or two characters, common in Chinese, use a slower substring scan. Results stay correct.
- `--fts` passes the query to FTS5 unchanged for `OR`, `NOT`, and `NEAR`. Each FTS5 term needs at least 3 characters (trigram tokenizer).

Filters:

```bash
--role user|assistant|tool    # who said it; tool = a tool call
--tool Bash                   # tool name substring, e.g. mcp__, Edit
--session 1a2b3c4d            # session ID prefix
--after 2026-10-01 --before 2026-10-08   # local time; --before is exclusive
--sort rank                   # bm25 order instead of newest first
--limit 50  --full  --json
```

Scope defaults to the current directory's project. Use `--project PATH` for another project, or `--all-projects` when the project is unknown. `CLAUDE_CONFIG_DIR` is honored; `--config-dir` overrides it.

## Expand a hit

Each hit prints a ref such as `1a2b3c4d:1184` (session prefix and 1-based JSONL line).

```bash
# Whole turn containing the hit: prompt, replies, tool calls
python3 <skill-dir>/scripts/session_search.py context 1a2b3c4d:1184

# Transcript lines around the hit instead of the turn
python3 <skill-dir>/scripts/session_search.py context 1a2b3c4d:1184 --radius 40

# Full tool input (complete command, SQL, file path) and its tool result
python3 <skill-dir>/scripts/session_search.py detail 1a2b3c4d:1184 --max-chars 0
```

Use `detail` on a `tool` row to recover the exact command or query that ran and what it returned. A Bash hit already shows its description and the first 1500 characters of the command.

## Other commands

```bash
python3 <skill-dir>/scripts/session_search.py recent 10     # latest turns, newest last
python3 <skill-dir>/scripts/session_search.py index --all-projects   # refresh + per-project stats
python3 <skill-dir>/scripts/session_search.py index --rebuild        # re-read the scope's transcripts
```

## Workflow

1. Search with two or three specific terms: an identifier, file name, table, or error string. These beat generic words.
2. With too many hits, add `--role`, `--tool`, or a date range. With none, drop a term or widen to `--all-projects`.
3. Run `context` on the best hit to read the turn, then `detail` for the exact command or result.
4. When reusing past work, cite the ref. The past session's facts may be stale, so check them against the current code or data before acting on them.

## What is indexed

- User prompts. Hook-injected `<system-reminder>` blocks and `isMeta` records are dropped. For messages wrapped in a `<channel ...>` tag (for example Slack), the attributes (`chat_id=… user=…`) are stored as metadata. Metadata is searchable and shown in brackets.
- Assistant text. Thinking blocks are not indexed.
- Tool calls: Bash description and command, file paths for Read, Write, and Edit, Agent description and prompt, and compact JSON input for other tools, MCP tools included.
- Not indexed: tool results (use `detail`) and subagent transcripts under `<session>/subagents/`. The parent session records the Agent call and the reply that summarized the subagent's result.

The index lives at `~/.cache/claude-session-search/index.db` (`$XDG_CACHE_HOME` and `CLAUDE_SESSION_SEARCH_DB` override it). Claude Code deletes transcripts after `cleanupPeriodDays` (30 by default). The index keeps rows of deleted transcripts, so it is the only copy of those sessions: do not delete it casually. `detail` needs the JSONL and does not work for them.

Transcripts can hold secrets and personal content. Quote only what the current request needs, and never reproduce credentials found in a hit.

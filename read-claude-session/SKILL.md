---
name: read-claude-session
description: Read, export, inspect, and summarize local Claude Code session transcripts stored as JSONL. Use when the user asks to read, review, continue from, compare, summarize, or answer questions about a Claude session, Claude Code session ID or UUID, session JSONL file, or the latest Claude conversation for a project.
---

# Read Claude Session

Export a Claude Code transcript to clean Markdown containing user prompts and assistant text. Omit thinking, tool calls, tool results, hook noise, and system reminders by default.

## Locate the session

Use the bundled exporter:

```bash
python3 <skill-dir>/scripts/session_export.py --list
python3 <skill-dir>/scripts/session_export.py <session-id-or-prefix> -o <output.md>
python3 <skill-dir>/scripts/session_export.py --latest -o <output.md>
```

The exporter resolves the current working directory automatically. Use these overrides when needed:

```bash
# Resolve a different source project
python3 <skill-dir>/scripts/session_export.py --project /path/to/project --list

# Search every Claude project when the source project is unknown
python3 <skill-dir>/scripts/session_export.py --all-projects --list
python3 <skill-dir>/scripts/session_export.py --all-projects <session-prefix>

# Read a known transcript directly
python3 <skill-dir>/scripts/session_export.py --session-file /path/to/session.jsonl
```

Honor `CLAUDE_CONFIG_DIR` automatically. Use `--config-dir` only to override it explicitly.

## Read and answer

Export to a temporary Markdown file when the transcript is large. Inspect its size and headings before reading it in chunks:

```bash
session_md="$(mktemp -t claude-session.XXXXXX.md)"
python3 <skill-dir>/scripts/session_export.py <session-prefix> -o "$session_md"
wc -l -c "$session_md"
rg -n '^## (User|Assistant)$' "$session_md"
```

Read enough of the exported conversation to answer the user's actual question. Preserve chronology and distinguish facts stated by the user from conclusions made by Claude. If the session is ambiguous, report the matching IDs and ask which one; never silently choose among multiple matches.

Treat transcripts as potentially sensitive. Do not reproduce secrets, credentials, or unrelated personal content. Summarize only what the current request needs.

## Optional detail

Include tool names only when the user asks for execution detail:

```bash
python3 <skill-dir>/scripts/session_export.py <session-prefix> --tools
```

Include truncated tool-result text only when it is necessary to answer the question or the user requests it:

```bash
python3 <skill-dir>/scripts/session_export.py <session-prefix> --results
```

---
name: read-codex-session
description: Read, export, inspect, and summarize local Codex session transcripts (rollout JSONL files). Use when the user asks to read, review, continue from, compare, summarize, or answer questions about a Codex session or thread, a Codex thread ID or `codex://threads/...` link, a rollout JSONL file, or the latest Codex conversation for a project.
---

# Read Codex Session

Export a Codex thread to clean Markdown containing user prompts and agent text. Omit reasoning, tool calls, tool output, injected context (AGENTS.md, environment, skills), and token accounting by default.

## Locate the session

Use the bundled exporter:

```bash
python3 <skill-dir>/scripts/session_export.py --list
python3 <skill-dir>/scripts/session_export.py <thread-id-or-prefix> -o <output.md>
python3 <skill-dir>/scripts/session_export.py --latest -o <output.md>
```

The exporter also accepts a `codex://threads/<id>` link in place of the ID. `--list` and `--latest` cover sessions whose working directory is the current directory or below it. Use these overrides when needed:

```bash
# Resolve a different source project
python3 <skill-dir>/scripts/session_export.py --project /path/to/project --list

# Every working directory; add archived threads and subagent threads
python3 <skill-dir>/scripts/session_export.py --all-projects --list
python3 <skill-dir>/scripts/session_export.py --all-projects --list --archived --subagents

# Read one rollout file directly
python3 <skill-dir>/scripts/session_export.py --session-file /path/to/rollout.jsonl
```

Lookup by ID searches active and archived sessions. A long thread spans several rollout files (`rollout-<time>-<thread-id>_<segment-id>.jsonl`); lookup by ID and `--latest` join them in order, while `--session-file` reads only the file given. `--list` marks such threads with `[N files]`.

Honor `CODEX_HOME` automatically. Use `--codex-home` only to override it explicitly.

## Read and answer

Export to a temporary Markdown file when the thread is large. Inspect its size and headings before reading it in chunks:

```bash
session_md="$(mktemp -t codex-session.XXXXXX.md)"
python3 <skill-dir>/scripts/session_export.py <thread-prefix> -o "$session_md"
wc -l -c "$session_md"
rg -n '^## (User|Assistant)$' "$session_md"
```

Read enough of the exported conversation to answer the user's actual question. Preserve chronology and distinguish facts stated by the user from conclusions made by the agent. If the prefix is ambiguous, report the matching IDs and ask which one; never silently choose among multiple matches.

Some user turns are not typed text: `<codex_delegation>` carries a request handed over from another thread (its `source_thread_id` names that thread), `<realtime_delegation>` carries a voice-mode transcript, and `<heartbeat>` is an automation firing. Say so when it matters to the answer.

Treat transcripts as potentially sensitive. Do not reproduce secrets, credentials, or unrelated personal content. Summarize only what the current request needs.

## Optional detail

The agent's progress messages are included by default. Keep only final answers with `--final-only`.

Include tool call summaries only when the user asks for execution detail:

```bash
python3 <skill-dir>/scripts/session_export.py <thread-prefix> --tools
```

Include truncated tool output only when it is necessary to answer the question or the user requests it:

```bash
python3 <skill-dir>/scripts/session_export.py <thread-prefix> --results
```

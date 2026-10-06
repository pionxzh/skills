#!/usr/bin/env python3
"""Export a local Codex session rollout (JSONL) to clean Markdown.

By default, retain only user inputs and agent text. Strip reasoning, tool
calls, tool output, injected context (AGENTS.md, environment, skills), and
token accounting.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator

SESSION_PREFIX_RE = re.compile(r"^[A-Za-z0-9-]+$")
# A long thread continues in further rollout files named
# rollout-<time>-<thread id>_<segment id>.jsonl.
ROLLOUT_ID_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<id>[0-9a-fA-F-]{36})(?:_[0-9a-fA-F-]{36})?$"
)
IMAGE_TAG_RE = re.compile(r"</?image\b[^>]*>")
# IDE clients prepend editor context (open files, selection) and put the
# typed request under this heading.
IDE_REQUEST_MARKER = "## My request for Codex:"
# Context Codex injects as user-role messages. A block that is entirely one
# of these is not something the user typed.
INJECTED_PREFIXES = ("# AGENTS.md instructions", "<INSTRUCTIONS>")
INJECTED_TAG_RE = re.compile(r"^<([a-zA-Z_][\w-]*)[^>]*>.*</\1>$", re.DOTALL)


def codex_home(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def rollout_id(path: Path) -> str:
    match = ROLLOUT_ID_RE.match(path.stem)
    return match.group("id") if match else path.stem


def rollout_files(home: Path, archived: bool) -> list[Path]:
    files = list((home / "sessions").glob("**/rollout-*.jsonl"))
    if archived:
        files.extend((home / "archived_sessions").glob("rollout-*.jsonl"))
    return files


def read_meta(path: Path) -> dict:
    """Return the session_meta payload from the first line of a rollout."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "session_meta":
                    payload = obj.get("payload")
                    return payload if isinstance(payload, dict) else {}
                break
    except OSError:
        pass
    return {}


def is_subagent(meta: dict) -> bool:
    source = meta.get("source")
    return isinstance(source, dict) and "subagent" in source


def in_project(meta: dict, project: Path) -> bool:
    cwd = meta.get("cwd")
    if not cwd:
        return False
    try:
        cwd_path = Path(cwd).resolve()
    except OSError:
        return False
    return cwd_path == project or project in cwd_path.parents


def thread_names(home: Path) -> dict[str, str]:
    """Map thread id to its latest name from session_index.jsonl."""
    names: dict[str, str] = {}
    index = home / "session_index.jsonl"
    try:
        with index.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("id") and obj.get("thread_name"):
                    names[obj["id"]] = obj["thread_name"]
    except OSError:
        pass
    return names


def select_files(
    home: Path,
    project: Path | None,
    archived: bool,
    subagents: bool,
) -> list[Path]:
    selected = []
    for path in rollout_files(home, archived):
        meta = read_meta(path)
        if not subagents and is_subagent(meta):
            continue
        if project is not None and not in_project(meta, project):
            continue
        selected.append(path)
    return selected


def group_threads(files: list[Path]) -> dict[str, list[Path]]:
    """Group rollout files by thread id, each group in chronological order."""
    threads: dict[str, list[Path]] = {}
    for path in files:
        threads.setdefault(rollout_id(path), []).append(path)
    for segments in threads.values():
        segments.sort(key=lambda path: path.name)
    return threads


def thread_mtime(segments: list[Path]) -> float:
    return max(path.stat().st_mtime for path in segments)


def find_session(home: Path, prefix: str) -> list[Path]:
    if not SESSION_PREFIX_RE.fullmatch(prefix):
        fail(
            "Session ID prefixes may contain only letters, digits, and hyphens. "
            "Use --session-file for a path."
        )
    threads = group_threads(
        [path for path in rollout_files(home, archived=True) if rollout_id(path).startswith(prefix)]
    )
    if prefix in threads:
        return threads[prefix]
    if not threads:
        fail(f"No Codex session matches {prefix!r}")
    if len(threads) > 1:
        details = "\n".join(f"  {thread_id}" for thread_id in sorted(threads)[:20])
        fail(f"Ambiguous session prefix {prefix!r}; matches:\n{details}")
    return next(iter(threads.values()))


def iter_records(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("payload"), dict):
                yield obj


def is_injected(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(INJECTED_PREFIXES) or bool(
        INJECTED_TAG_RE.fullmatch(stripped)
    )


def strip_ide_context(text: str) -> str:
    if IDE_REQUEST_MARKER in text:
        return text.rsplit(IDE_REQUEST_MARKER, 1)[1].strip()
    return text


def user_item_text(content: object) -> str:
    """Text of a UserMessage item: typed text plus attachment markers."""
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        text = strip_ide_context(IMAGE_TAG_RE.sub("", str(block.get("text", "")))).strip()
        if kind == "text" and text:
            parts.append(text)
        elif kind in {"local_image", "image"}:
            parts.append(f"[image: {block.get('path') or block.get('url') or ''}]")
    return "\n\n".join(parts)


def response_user_text(content: object) -> str:
    """Text of a user-role response item, minus injected context blocks."""
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "input_text":
            continue
        text = strip_ide_context(IMAGE_TAG_RE.sub("", str(block.get("text", "")))).strip()
        if text and not is_injected(text):
            parts.append(text)
    return "\n\n".join(parts)


def message_text(content: object) -> str:
    parts = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") in {"Text", "output_text", "text"}:
            text = str(block.get("text", "")).strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def one_line(value: object, limit: int = 100) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def summarize_tool(payload: dict) -> str:
    kind = payload.get("type", "")
    name = payload.get("name") or kind.removesuffix("_call")
    if kind == "function_call":
        raw = payload.get("arguments", "")
        try:
            args = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            args = raw
        if isinstance(args, dict):
            detail = args.get("cmd") or args.get("command") or args.get("path")
            if isinstance(detail, list):
                detail = " ".join(map(str, detail))
            return f"{name}: {one_line(detail)}" if detail else name
        return f"{name}: {one_line(args)}" if args else name
    if kind == "custom_tool_call":
        return f"{name}: {one_line(payload.get('input', ''))}"
    if kind == "web_search_call":
        action = payload.get("action") or {}
        detail = action.get("query") or action.get("url") or action.get("type") or ""
        return f"web_search: {one_line(detail)}"
    return str(name)


def tool_output_text(output: object) -> str:
    if isinstance(output, str):
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            return output.strip()
        if isinstance(decoded, dict) and "output" in decoded:
            return str(decoded["output"]).strip()
        return output.strip()
    if isinstance(output, list):
        return "".join(
            str(block.get("text", ""))
            for block in output
            if isinstance(block, dict)
        ).strip()
    return ""


def session_label(path: Path, names: dict[str, str]) -> str:
    name = names.get(rollout_id(path))
    if name:
        return name
    try:
        for obj in iter_records(path):
            payload = obj["payload"]
            item = payload.get("item") or {}
            text = ""
            if obj.get("type") == "event_msg" and item.get("type") == "UserMessage":
                text = user_item_text(item.get("content"))
            elif obj.get("type") == "response_item" and payload.get("role") == "user":
                text = response_user_text(payload.get("content"))
            if text:
                return one_line(text, 80)
    except OSError:
        pass
    return "(empty)"


def list_sessions(home: Path, files: list[Path], count: int) -> None:
    names = thread_names(home)
    threads = sorted(group_threads(files).items(), key=lambda kv: thread_mtime(kv[1]), reverse=True)
    for thread_id, segments in threads[:count]:
        size_mb = sum(path.stat().st_size for path in segments) / (1024 * 1024)
        modified = datetime.fromtimestamp(thread_mtime(segments)).strftime("%Y-%m-%d %H:%M")
        meta = read_meta(segments[0])
        cwd = Path(meta.get("cwd") or "?").name
        flags = " [sub]" if is_subagent(meta) else ""
        if any(path.parent.name == "archived_sessions" for path in segments):
            flags += " [archived]"
        if len(segments) > 1:
            flags += f" [{len(segments)} files]"
        print(
            f"{thread_id[:13]}  {size_mb:6.1f}MB  {modified}  {cwd}  "
            f"{session_label(segments[0], names)}{flags}"
        )


def export_session(
    segments: list[Path],
    title: str | None,
    include_tools: bool = False,
    include_results: bool = False,
    max_result_len: int | None = 500,
    final_only: bool = False,
) -> str:
    # Newer rollouts record what the user typed and what the agent said as
    # item_completed events; the matching response items carry injected
    # context and duplicate the agent text. Use the events when a file has
    # them.
    def item_type(obj: dict) -> str | None:
        if obj.get("type") != "event_msg":
            return None
        item = obj["payload"].get("item")
        return item.get("type") if isinstance(item, dict) else None

    turns: list[dict[str, object]] = []
    meta: dict = {}

    def current() -> dict[str, object] | None:
        return turns[-1] if turns else None

    for path in segments:
        first_user_in_file = True
        records = list(iter_records(path))
        user_events = any(item_type(obj) == "UserMessage" for obj in records)
        agent_events = any(item_type(obj) == "AgentMessage" for obj in records)
        for obj in records:
            kind = obj.get("type")
            payload = obj["payload"]
            if kind == "session_meta":
                meta = meta or payload
                continue

            user_text = ""
            agent_text = ""
            phase = None
            if kind == "event_msg":
                item = payload.get("item")
                if isinstance(item, dict):
                    if item.get("type") == "UserMessage" and user_events:
                        user_text = user_item_text(item.get("content"))
                    elif item.get("type") == "AgentMessage" and agent_events:
                        agent_text = message_text(item.get("content"))
                        phase = item.get("phase")
            elif kind == "response_item":
                role = payload.get("role")
                ptype = payload.get("type", "")
                if ptype == "message" and role == "user" and not user_events:
                    user_text = response_user_text(payload.get("content"))
                elif ptype == "message" and role == "assistant" and not agent_events:
                    agent_text = message_text(payload.get("content"))
                    phase = payload.get("phase")
                elif ptype.endswith("_call") and include_tools and current():
                    current()["tools"].append(summarize_tool(payload))
                elif ptype.endswith("_call_output") and include_results and current():
                    text = tool_output_text(payload.get("output"))
                    if text:
                        if max_result_len and len(text) > max_result_len:
                            text = text[:max_result_len] + "\n... (truncated)"
                        current()["last_result"] = text

            if user_text:
                turn = {"user_text": user_text, "agent_texts": [], "tools": []}
                # A continuation file opens by resending the message that
                # ended the previous file unanswered, sometimes with more
                # text appended; keep the resent turn only.
                replayed = (
                    first_user_in_file
                    and path is not segments[0]
                    and current() is not None
                    and not current()["agent_texts"]
                    and user_text.startswith(str(current()["user_text"]))
                )
                first_user_in_file = False
                if replayed:
                    turns[-1] = turn
                else:
                    turns.append(turn)
            elif agent_text and current():
                if final_only and phase == "commentary":
                    continue
                current()["agent_texts"].append(agent_text)

    heading = title or f"Session {rollout_id(segments[0])[:13]}"
    lines = [f"# {heading}", ""]
    if meta.get("cwd"):
        lines.extend([f"Working directory: `{meta['cwd']}`", ""])
    for turn in turns:
        lines.extend(["## User", "", str(turn["user_text"]), ""])
        agent_text = "\n\n".join(str(text) for text in turn["agent_texts"])
        if agent_text:
            lines.extend(["## Assistant", "", agent_text, ""])
        tools = turn["tools"]
        if include_tools and isinstance(tools, list) and tools:
            lines.extend(["<details><summary>Tools used</summary>", ""])
            lines.extend(f"- {tool}" for tool in tools)
            lines.extend(["", "</details>", ""])
        last_result = turn.get("last_result")
        if include_results and last_result:
            lines.extend(
                [
                    "<details><summary>Last tool result</summary>",
                    "",
                    "```text",
                    str(last_result),
                    "```",
                    "",
                    "</details>",
                    "",
                ]
            )
        lines.extend(["---", ""])
    return "\n".join(lines)


def fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a Codex session to clean Markdown")
    parser.add_argument(
        "session", nargs="?", help="Thread ID, unique prefix, or codex://threads/<id> link"
    )
    parser.add_argument("-o", "--output", help="Output Markdown file (default: stdout)")
    parser.add_argument("--list", action="store_true", help="List available sessions")
    parser.add_argument("--latest", action="store_true", help="Export the newest session")
    parser.add_argument("--limit", type=int, default=20, help="Maximum sessions shown by --list")
    parser.add_argument("--project", help="Source project path (default: current directory)")
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="List or pick sessions from every working directory",
    )
    parser.add_argument("--session-file", help="Exact Codex rollout JSONL file")
    parser.add_argument(
        "--archived", action="store_true", help="Include archived sessions in --list/--latest"
    )
    parser.add_argument(
        "--subagents", action="store_true", help="Include subagent threads in --list/--latest"
    )
    parser.add_argument("--codex-home", help="Override CODEX_HOME")
    parser.add_argument("--tools", action="store_true", help="Include tool call summaries")
    parser.add_argument(
        "--results", action="store_true", help="Include the last tool output per turn"
    )
    parser.add_argument(
        "--max-result-len",
        type=int,
        default=500,
        help="Maximum characters per result (default: 500; 0 means unlimited)",
    )
    parser.add_argument(
        "--final-only",
        action="store_true",
        help="Drop the agent's progress (commentary) messages, keep final answers",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    home = codex_home(args.codex_home)
    if args.session_file:
        segments = [Path(args.session_file).expanduser()]
    else:
        if not (home / "sessions").is_dir():
            fail(f"Codex sessions directory not found: {home / 'sessions'}")
        project = None
        if not args.all_projects:
            project = (Path(args.project).expanduser() if args.project else Path.cwd()).resolve()
        if args.list or args.latest:
            files = select_files(home, project, args.archived, args.subagents)
            if not files:
                fail("No Codex sessions found for the selected scope")
            if args.list:
                list_sessions(home, files, args.limit)
                return
            segments = max(group_threads(files).values(), key=thread_mtime)
        elif args.session:
            segments = find_session(home, args.session.removeprefix("codex://threads/"))
        else:
            fail("Provide a session ID, --latest, --list, or --session-file")

    for path in segments:
        if not path.is_file():
            fail(f"Session file not found: {path}")

    max_result_len = None if args.max_result_len == 0 else args.max_result_len
    markdown = export_session(
        segments,
        thread_names(home).get(rollout_id(segments[0])),
        include_tools=args.tools,
        include_results=args.results,
        max_result_len=max_result_len,
        final_only=args.final_only,
    )
    if args.output:
        output = Path(args.output).expanduser()
        output.write_text(markdown, encoding="utf-8")
        source = segments[0] if len(segments) == 1 else f"{len(segments)} rollout files"
        print(f"Exported {source} to {output}", file=sys.stderr)
    else:
        try:
            print(markdown)
        except BrokenPipeError:
            # Reader closed early (e.g. piped into head); not an error.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


if __name__ == "__main__":
    main()

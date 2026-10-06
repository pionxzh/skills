#!/usr/bin/env python3
"""Export a local Claude Code session JSONL to clean Markdown.

By default, retain only user inputs and assistant text. Strip thinking blocks,
tool calls, tool results, system reminders, and hook noise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
CHANNEL_RE = re.compile(r"<channel\s+[^>]*>(.*?)</channel>", re.DOTALL)
SESSION_PREFIX_RE = re.compile(r"^[A-Za-z0-9-]+$")
MAX_PROJECT_KEY_LENGTH = 200


def claude_config_dir(override: str | None = None) -> Path:
    """Return Claude Code's config directory."""
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
    """Return plausible transcript directories for a project path."""
    expanded = project.expanduser()
    paths: list[Path] = []
    for value in (expanded.absolute(), expanded.resolve()):
        candidate = root / project_key(value)
        if candidate.is_dir() and candidate not in paths:
            paths.append(candidate)

    if paths:
        return paths

    # Legacy Claude releases and moved/symlinked projects may have a different
    # suffix. Restrict fallback matching and let callers reject ambiguity.
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", str(expanded.absolute()))
    prefix = sanitized[:MAX_PROJECT_KEY_LENGTH]
    for candidate in sorted(root.glob(f"{prefix}*")):
        if candidate.is_dir() and candidate not in paths:
            paths.append(candidate)
    return paths


def all_project_dirs(root: Path) -> list[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir())


def session_files(project_dirs: Iterable[Path]) -> list[Path]:
    files = []
    for project_dir in project_dirs:
        files.extend(project_dir.glob("*.jsonl"))
    return files


def find_session(
    root: Path,
    project_dirs: list[Path],
    prefix: str,
    search_all: bool,
) -> Path:
    if not SESSION_PREFIX_RE.fullmatch(prefix):
        fail(
            "Session ID prefixes may contain only letters, digits, and hyphens. "
            "Use --session-file for a path."
        )

    dirs = all_project_dirs(root) if search_all else project_dirs
    matches = []
    for project_dir in dirs:
        matches.extend(sorted(project_dir.glob(f"{prefix}*.jsonl")))

    # A project hint is convenient, but a UUID identifies the session. Fall
    # back globally when the hinted/current project has no match.
    if not matches and not search_all:
        for project_dir in all_project_dirs(root):
            if project_dir in dirs:
                continue
            matches.extend(sorted(project_dir.glob(f"{prefix}*.jsonl")))

    exact = [path for path in matches if path.stem == prefix]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        matches = exact

    if not matches:
        fail(f"No Claude session matches {prefix!r}")
    if len(matches) > 1:
        details = "\n".join(f"  {path.stem}  ({path.parent.name})" for path in matches[:20])
        fail(f"Ambiguous session prefix {prefix!r}; matches:\n{details}")
    return matches[0]


def latest_session(project_dirs: list[Path]) -> Path:
    files = session_files(project_dirs)
    if not files:
        fail("No Claude sessions found for the selected project scope")
    return max(files, key=lambda path: path.stat().st_mtime)


def session_label(path: Path) -> str:
    title = None
    first_user = None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "ai-title" and not title:
                    title = obj.get("aiTitle", "")
                if obj.get("type") == "user" and not first_user:
                    content = obj.get("message", {}).get("content", "")
                    text = extract_user_text(content)
                    if text:
                        first_user = " ".join(text.split())[:80]
                if title and first_user:
                    break
    except OSError:
        pass
    return title or first_user or "(empty)"


def list_sessions(project_dirs: list[Path], count: int) -> None:
    files = sorted(
        session_files(project_dirs),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[:count]:
        stat = path.stat()
        size_mb = stat.st_size / (1024 * 1024)
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        print(
            f"{path.stem[:8]}  {size_mb:6.1f}MB  {modified}  "
            f"{path.parent.name}  {session_label(path)}"
        )


def clean_text(text: str) -> str:
    return SYSTEM_REMINDER_RE.sub("", text).strip()


def extract_user_text(content: object) -> str | None:
    """Extract human-visible user text while ignoring tool results."""
    if isinstance(content, str):
        match = CHANNEL_RE.search(content)
        return clean_text(match.group(1) if match else content)
    if isinstance(content, list):
        texts = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = clean_text(str(block.get("text", "")))
            if text:
                texts.append(text)
        return "\n\n".join(texts) or None
    return None


def summarize_tool(name: str, input_: object) -> str:
    if not isinstance(input_, dict) or not input_:
        return name
    if name in {"Read", "Write", "Edit"}:
        filename = str(input_.get("file_path", "")).rsplit("/", 1)[-1]
        return f"{name} {filename}".rstrip()
    if name == "Bash":
        detail = input_.get("description") or input_.get("command") or ""
        return f"Bash: {str(detail)[:100]}"
    if name == "Agent":
        return f"Agent: {str(input_.get('description', ''))[:100]}"
    return name


def tool_result_text(content: object) -> str:
    if isinstance(content, str):
        return clean_text(content)
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
        return clean_text("".join(texts))
    return ""


def export_session(
    jsonl_path: Path,
    include_tools: bool = False,
    include_results: bool = False,
    max_result_len: int | None = 500,
) -> str:
    """Parse a Claude transcript and return clean Markdown."""
    turns: dict[str, dict[str, object]] = {}
    turn_order: list[str] = []
    title = None
    current_prompt_id = None

    with jsonl_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            message_type = obj.get("type", "")
            if message_type == "ai-title" and not title:
                title = obj.get("aiTitle", "")
                continue

            prompt_id = obj.get("promptId", "")
            if prompt_id:
                current_prompt_id = prompt_id

            if message_type == "user":
                content = obj.get("message", {}).get("content", "")
                user_text = extract_user_text(content)
                if user_text and current_prompt_id:
                    if current_prompt_id not in turns:
                        turns[current_prompt_id] = {
                            "user_text": user_text,
                            "assistant_texts": [],
                            "tools": [],
                        }
                        turn_order.append(current_prompt_id)
                    elif not turns[current_prompt_id]["user_text"]:
                        turns[current_prompt_id]["user_text"] = user_text

                if isinstance(content, list) and include_results and current_prompt_id:
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        text = tool_result_text(block.get("content", ""))
                        if text and current_prompt_id in turns:
                            if max_result_len and len(text) > max_result_len:
                                text = text[:max_result_len] + "\n... (truncated)"
                            turns[current_prompt_id]["last_result"] = text

            elif message_type == "assistant":
                content = obj.get("message", {}).get("content", [])
                if not isinstance(content, list):
                    continue
                prompt_id = current_prompt_id
                if not prompt_id or prompt_id not in turns:
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text = clean_text(str(block.get("text", "")))
                        if text:
                            turns[prompt_id]["assistant_texts"].append(text)
                    elif block_type == "tool_use" and include_tools:
                        turns[prompt_id]["tools"].append(
                            summarize_tool(
                                str(block.get("name", "")),
                                block.get("input", {}),
                            )
                        )

    lines = [f"# {title}" if title else f"# Session {jsonl_path.stem[:8]}", ""]
    for prompt_id in turn_order:
        turn = turns[prompt_id]
        user_text = str(turn["user_text"])
        if not user_text:
            continue
        lines.extend(["## User", "", user_text, ""])

        assistant_text = "\n\n".join(str(text) for text in turn["assistant_texts"])
        if assistant_text:
            lines.extend(["## Assistant", "", assistant_text, ""])

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
    parser = argparse.ArgumentParser(
        description="Export a Claude Code session to clean Markdown"
    )
    parser.add_argument("session", nargs="?", help="Session UUID or unique prefix")
    parser.add_argument("-o", "--output", help="Output Markdown file (default: stdout)")
    parser.add_argument("--list", action="store_true", help="List available sessions")
    parser.add_argument("--latest", action="store_true", help="Export the newest session")
    parser.add_argument(
        "--limit", type=int, default=20, help="Maximum sessions shown by --list"
    )
    parser.add_argument("--project", help="Source project path (default: current directory)")
    parser.add_argument("--project-dir", help="Exact Claude project transcript directory")
    parser.add_argument("--session-file", help="Exact Claude session JSONL file")
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="Search or list sessions across every Claude project",
    )
    parser.add_argument("--config-dir", help="Override CLAUDE_CONFIG_DIR")
    parser.add_argument("--tools", action="store_true", help="Include tool summaries")
    parser.add_argument(
        "--results", action="store_true", help="Include the last tool result per turn"
    )
    parser.add_argument(
        "--max-result-len",
        type=int,
        default=500,
        help="Maximum characters per result (default: 500; 0 means unlimited)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.session_file:
        jsonl_path = Path(args.session_file).expanduser()
    else:
        config = claude_config_dir(args.config_dir)
        root = projects_root(config)

        if args.project_dir:
            selected_dirs = [Path(args.project_dir).expanduser()]
        elif args.all_projects:
            selected_dirs = all_project_dirs(root)
        else:
            selected_dirs = candidate_project_dirs(
                root, Path(args.project).expanduser() if args.project else Path.cwd()
            )

        if args.list:
            if not selected_dirs:
                fail("No Claude project directory matches the selected project")
            list_sessions(selected_dirs, args.limit)
            return

        if args.latest:
            if not selected_dirs:
                fail("No Claude project directory matches the selected project")
            jsonl_path = latest_session(selected_dirs)
        elif args.session:
            jsonl_path = find_session(root, selected_dirs, args.session, args.all_projects)
        else:
            fail("Provide a session ID, --latest, --list, or --session-file")

    if not jsonl_path.is_file():
        fail(f"Session file not found: {jsonl_path}")

    max_result_len = None if args.max_result_len == 0 else args.max_result_len
    markdown = export_session(
        jsonl_path,
        include_tools=args.tools,
        include_results=args.results,
        max_result_len=max_result_len,
    )
    if args.output:
        output = Path(args.output).expanduser()
        output.write_text(markdown, encoding="utf-8")
        print(f"Exported {jsonl_path} to {output}", file=sys.stderr)
    else:
        print(markdown)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run Codex as a delegated worker from another agent.

Each job is one Codex thread. Every turn runs `codex exec --json` (or
`codex exec resume`) as a child process that exits when the turn ends, so no
app-server or broker outlives the turn. Events, the final message, and the
thread id are kept in a job directory for peeking and resuming.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
THREAD_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

# Unless a job allows pushing, every push URL is rewritten to a scheme git
# has no helper for, so `git push` (and `gh pr create`, which pushes) fails
# with an error that names the block. Fetching is unaffected. This is a
# guardrail against accidents, not a security boundary.
PUSH_BLOCK_SCHEME = "push-disabled-by-codex-delegate://"
PUSH_URL_PREFIXES = ["/", "file://", "git@", "ssh://", "https://", "http://"]
NO_PUSH_NOTE = (
    "\n\n<delegation_rules>\nDo not push, open pull requests, or publish anything. "
    "The agent that delegated this task handles that. Pushes from this session are "
    "blocked.\n</delegation_rules>\n"
)


def push_block_env() -> dict[str, str]:
    env = dict(os.environ)
    base = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    for offset, prefix in enumerate(PUSH_URL_PREFIXES):
        env[f"GIT_CONFIG_KEY_{base + offset}"] = f"url.{PUSH_BLOCK_SCHEME}.pushInsteadOf"
        env[f"GIT_CONFIG_VALUE_{base + offset}"] = prefix
    env["GIT_CONFIG_COUNT"] = str(base + len(PUSH_URL_PREFIXES))
    return env


def jobs_root() -> Path:
    configured = os.environ.get("CODEX_JOBS_DIR")
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "codex-jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


class Job:
    def __init__(self, name: str):
        if not NAME_RE.fullmatch(name):
            fail("Job names may contain letters, digits, '.', '_' and '-' (max 64).")
        self.name = name
        self.dir = jobs_root() / name

    @property
    def meta_path(self) -> Path:
        return self.dir / "meta.json"

    def exists(self) -> bool:
        return self.meta_path.is_file()

    def load(self) -> dict:
        if not self.exists():
            fail(f"No job named {self.name!r} (see `list`)")
        return json.loads(self.meta_path.read_text())

    def save(self, meta: dict) -> None:
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        tmp = self.meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        tmp.replace(self.meta_path)

    def turn_file(self, turn: int, suffix: str) -> Path:
        return self.dir / f"turn-{turn}.{suffix}"

    def running_pid(self) -> int | None:
        pid_file = self.dir / "pid"
        try:
            pid = int(pid_file.read_text())
        except (OSError, ValueError):
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        return pid


def read_events(path: Path) -> list[dict]:
    events = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    events.append(obj)
    except OSError:
        pass
    return events


def turn_status(events: list[dict], running: bool) -> str:
    if running:
        return "running"
    kinds = [event.get("type") for event in events]
    if "turn.completed" in kinds:
        return "completed"
    if "turn.failed" in kinds or "error" in kinds:
        return "failed"
    return "interrupted"


def common_codex_args(meta: dict, args: argparse.Namespace) -> list[str]:
    # Without an explicit choice, Codex applies its own config's sandbox_mode.
    sandbox = getattr(args, "sandbox", None) or meta.get("sandbox")
    result = ["--json", "--skip-git-repo-check"]
    if sandbox:
        result += ["-c", f'sandbox_mode="{sandbox}"']
        meta["sandbox"] = sandbox
    model = args.model or meta.get("model")
    effort = args.effort or meta.get("effort")
    if model:
        result += ["-m", model]
        meta["model"] = model
    if effort:
        result += ["-c", f'model_reasoning_effort="{effort}"']
        meta["effort"] = effort
    return result


def run_turn(job: Job, meta: dict, command: list[str], prompt: str, cwd: Path) -> int:
    """Run one Codex turn in the foreground, recording its events."""
    allow_push = meta.get("allow_push", False)
    if not allow_push and meta.get("kind") == "task":
        prompt += NO_PUSH_NOTE
    turn = meta.get("turns", 0) + 1
    meta["turns"] = turn
    events_path = job.turn_file(turn, "jsonl")
    last_path = job.turn_file(turn, "last.md")
    command += ["-o", str(last_path)]
    job.save(meta)

    with events_path.open("w") as events, job.turn_file(turn, "err").open("w") as errors:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=None if allow_push else push_block_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
        )
        (job.dir / "pid").write_text(str(process.pid))
        assert process.stdin and process.stdout
        process.stdin.write(prompt)
        process.stdin.close()
        try:
            for line in process.stdout:
                events.write(line)
                events.flush()
                if not meta.get("thread_id") and '"thread.started"' in line:
                    try:
                        meta["thread_id"] = json.loads(line).get("thread_id")
                        job.save(meta)
                    except json.JSONDecodeError:
                        pass
        except KeyboardInterrupt:
            process.send_signal(signal.SIGINT)
        code = process.wait()
    (job.dir / "pid").unlink(missing_ok=True)
    job.save(meta)

    status = turn_status(read_events(events_path), running=False)
    print(f"job {job.name}  turn {turn}  {status}  thread {meta.get('thread_id', '?')}")
    if last_path.is_file() and last_path.read_text().strip():
        print()
        print(last_path.read_text().rstrip())
    elif status != "completed":
        print(f"(no final message; see `peek {job.name}` and {job.turn_file(turn, 'err')})")
    return code


def read_prompt(args: argparse.Namespace, required: bool = True) -> str:
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text()
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        prompt = ""
    if required and not prompt.strip():
        fail("Provide the prompt on stdin or with --prompt-file")
    return prompt


def cmd_start(args: argparse.Namespace) -> int:
    job = Job(args.name)
    if job.exists():
        fail(f"Job {args.name!r} already exists; `resume` it or pick another name")
    cwd = Path(args.cd).expanduser().resolve()
    if not cwd.is_dir():
        fail(f"Not a directory: {cwd}")
    prompt = read_prompt(args)
    job.dir.mkdir(parents=True)
    meta = {"name": args.name, "cwd": str(cwd), "kind": "task", "turns": 0,
            "allow_push": args.allow_push}
    command = ["codex", "exec", *common_codex_args(meta, args)]
    command += [f"--image={image}" for image in args.image]
    command += [f"--add-dir={directory}" for directory in args.add_dir]
    command += ["-C", str(cwd)]
    return run_turn(job, meta, command, prompt, cwd)


def cmd_review(args: argparse.Namespace) -> int:
    job = Job(args.name)
    if job.exists():
        fail(f"Job {args.name!r} already exists; pick another name")
    cwd = Path(args.cd).expanduser().resolve()
    prompt = read_prompt(args, required=False)
    job.dir.mkdir(parents=True)
    meta = {"name": args.name, "cwd": str(cwd), "kind": "review", "turns": 0}
    command = ["codex", "exec", "review", *common_codex_args(meta, args)]
    if args.uncommitted:
        command.append("--uncommitted")
    if args.base:
        command += ["--base", args.base]
    if args.commit:
        command += ["--commit", args.commit]
    if prompt.strip():
        command.append("-")
    return run_turn(job, meta, command, prompt, cwd)


def cmd_resume(args: argparse.Namespace) -> int:
    job = Job(args.name)
    meta = job.load()
    if job.running_pid():
        fail(f"Job {args.name!r} is still running; wait for it or `stop` it first")
    if not meta.get("thread_id"):
        fail(f"Job {args.name!r} has no thread id yet; start it again under a new name")
    prompt = read_prompt(args)
    if args.allow_push:
        meta["allow_push"] = True
    command = ["codex", "exec", "resume", *common_codex_args(meta, args)]
    command += [f"--image={image}" for image in args.image]
    command += [meta["thread_id"], "-"]
    return run_turn(job, meta, command, prompt, Path(meta["cwd"]))


def cmd_stop(args: argparse.Namespace) -> int:
    job = Job(args.name)
    job.load()
    pid = job.running_pid()
    if not pid:
        print(f"Job {args.name!r} is not running")
        return 0
    # SIGINT takes Codex's interrupt path: the turn is recorded as aborted
    # and its running commands are cleaned up.
    os.kill(pid, signal.SIGINT)
    for _ in range(50):
        if not job.running_pid():
            break
        time.sleep(0.2)
    print(f"Interrupted job {args.name!r}; `resume` it with corrected instructions")
    return 0


def one_line(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def describe(event: dict, full: bool) -> str | None:
    kind = event.get("type", "")
    item = event.get("item") or {}
    itype = item.get("type")
    limit = 10_000 if full else 300
    if kind == "item.completed" and itype == "agent_message":
        return f"agent: {item.get('text', '').strip() if full else one_line(item.get('text', ''), limit)}"
    if itype == "command_execution":
        command = one_line(item.get("command", ""), 160)
        if kind == "item.started":
            return f"$ {command}  (running)"
        if kind == "item.completed":
            output = (item.get("aggregated_output") or "").strip()
            tail = f"\n    {one_line(output[-400:], 400)}" if full and output else ""
            return f"$ {command}  (exit {item.get('exit_code')}){tail}"
    if kind == "item.completed" and itype == "file_change":
        changes = item.get("changes") or []
        paths = ", ".join(f"{c.get('kind', '?')} {c.get('path', '?')}" for c in changes)
        return f"files: {paths}"
    if kind == "item.completed" and itype in {"mcp_tool_call", "web_search", "todo_list"}:
        return f"{itype}: {one_line(json.dumps(item, ensure_ascii=False), 160)}"
    if kind == "item.completed" and itype == "error":
        return None  # config warnings and similar; shown in the .err file
    if kind == "turn.completed":
        usage = event.get("usage") or {}
        return f"turn completed  (in {usage.get('input_tokens', '?')}, out {usage.get('output_tokens', '?')} tokens)"
    if kind in {"turn.failed", "error"}:
        return f"{kind}: {one_line(json.dumps(event.get('error') or event, ensure_ascii=False), 300)}"
    return None


def cmd_peek(args: argparse.Namespace) -> int:
    job = Job(args.name)
    meta = job.load()
    turn = args.turn or meta.get("turns", 0)
    events = read_events(job.turn_file(turn, "jsonl"))
    running = bool(job.running_pid()) and turn == meta.get("turns")
    print(f"job {job.name}  turn {turn}/{meta.get('turns', 0)}  {turn_status(events, running)}  "
          f"thread {meta.get('thread_id', '?')}  cwd {meta.get('cwd')}")
    # A started command is superseded by its completion event.
    completed_ids = {
        (event.get("item") or {}).get("id")
        for event in events
        if event.get("type") == "item.completed"
    }
    lines = []
    for event in events:
        item_id = (event.get("item") or {}).get("id")
        if event.get("type") == "item.started" and item_id in completed_ids:
            continue
        line = describe(event, args.full)
        if line:
            lines.append(line)
    shown = lines if args.all else lines[-args.tail:]
    if len(shown) < len(lines):
        print(f"... {len(lines) - len(shown)} earlier entries (use --all)")
    for line in shown:
        print(line)
    return 0


def cmd_result(args: argparse.Namespace) -> int:
    job = Job(args.name)
    meta = job.load()
    turn = args.turn or meta.get("turns", 0)
    if job.running_pid() and turn == meta.get("turns"):
        print(f"Job {args.name!r} is still running (see `peek`)")
        return 0
    last = job.turn_file(turn, "last.md")
    if last.is_file() and last.read_text().strip():
        print(last.read_text().rstrip())
    else:
        print(f"Turn {turn} has no final message; see `peek {args.name} --turn {turn}`")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = []
    for meta_path in jobs_root().glob("*/meta.json"):
        job = Job(meta_path.parent.name)
        meta = json.loads(meta_path.read_text())
        events = read_events(job.turn_file(meta.get("turns", 0), "jsonl"))
        status = turn_status(events, bool(job.running_pid()))
        rows.append((meta.get("updated", ""), job.name, meta, status))
    for updated, name, meta, status in sorted(rows, reverse=True):
        print(f"{name:24} {status:11} turns {meta.get('turns', 0):<3} {updated}  "
              f"{meta.get('kind', 'task'):6} {meta.get('thread_id', '?')}  {meta.get('cwd')}")
    if not rows:
        print(f"No jobs in {jobs_root()}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def sandbox_option(p: argparse.ArgumentParser) -> None:
        group = p.add_mutually_exclusive_group()
        group.add_argument(
            "--sandbox",
            choices=["read-only", "workspace-write", "danger-full-access"],
            help="Sandbox for this job (default: the Codex config's sandbox_mode)",
        )
        group.add_argument(
            "--read-only", dest="sandbox", action="store_const", const="read-only",
            help="Shorthand for --sandbox read-only",
        )

    def runtime_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--model", help="Codex model (default: Codex config)")
        p.add_argument("--effort", help="Reasoning effort, passed through unchecked")
        p.add_argument("--prompt-file", help="Read the prompt from a file instead of stdin")

    start = sub.add_parser("start", help="Start a new Codex thread (first turn)")
    start.add_argument("name")
    start.add_argument("--cd", default=".", help="Working directory (default: current)")
    sandbox_option(start)
    start.add_argument("--allow-push", action="store_true",
                       help="Let Codex push (blocked by default); only when the user asked")
    start.add_argument("--image", action="append", default=[], help="Attach an image (repeatable)")
    start.add_argument("--add-dir", action="append", default=[], help="Extra writable directory")
    runtime_options(start)
    start.set_defaults(func=cmd_start)

    review = sub.add_parser("review", help="Run `codex exec review` as a job")
    review.add_argument("name")
    review.add_argument("--cd", default=".", help="Repository directory (default: current)")
    sandbox_option(review)
    target = review.add_mutually_exclusive_group(required=True)
    target.add_argument("--uncommitted", action="store_true")
    target.add_argument("--base", help="Review changes against this branch")
    target.add_argument("--commit", help="Review the changes of this commit")
    runtime_options(review)
    review.set_defaults(func=cmd_review)

    resume = sub.add_parser("resume", help="Send the next turn to a job's thread")
    resume.add_argument("name")
    sandbox_option(resume)
    resume.add_argument("--allow-push", action="store_true",
                        help="Lift the push block for this job; only when the user asked")
    resume.add_argument("--image", action="append", default=[], help="Attach an image (repeatable)")
    runtime_options(resume)
    resume.set_defaults(func=cmd_resume)

    stop = sub.add_parser("stop", help="Interrupt a running turn (SIGINT)")
    stop.add_argument("name")
    stop.set_defaults(func=cmd_stop)

    peek = sub.add_parser("peek", help="Show what a turn has done so far")
    peek.add_argument("name")
    peek.add_argument("--turn", type=int, help="Turn number (default: latest)")
    peek.add_argument("--tail", type=int, default=20, help="Entries shown (default: 20)")
    peek.add_argument("--all", action="store_true", help="Show every entry")
    peek.add_argument("--full", action="store_true", help="Untruncated messages, output tails")
    peek.set_defaults(func=cmd_peek)

    result = sub.add_parser("result", help="Print a turn's final message")
    result.add_argument("name")
    result.add_argument("--turn", type=int, help="Turn number (default: latest)")
    result.set_defaults(func=cmd_result)

    listing = sub.add_parser("list", help="List jobs")
    listing.set_defaults(func=cmd_list)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

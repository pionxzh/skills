---
name: codex-delegate
description: Delegate a task, a code review, or image generation to OpenAI Codex running locally, then supervise it across turns. Use when the user asks to hand work to Codex, get a second opinion or review from Codex, have Codex generate or edit an image, or continue, check on, redirect, or stop a Codex job started earlier.
---

# Delegate to Codex

`scripts/codex_job.py` runs Codex as a worker. A **job** is one Codex thread under a name you choose. Each turn runs `codex exec` as a child process that exits when the turn ends, so nothing keeps running between turns. You supervise: start a turn, peek while it runs, read the result, then resume with the next instruction, or stop it and resume with a correction.

```bash
J=<skill-dir>/scripts/codex_job.py
```

## Start a job

Run `start` with Bash `run_in_background: true`; the notification arrives when the turn ends, and the task output holds the final message. Pass the prompt on stdin:

```bash
python3 $J start fix-null-deref --cd /path/to/repo <<'EOF'
<task>...</task>
EOF
```

- The sandbox follows the Codex config's `sandbox_mode`, the same as when the user runs Codex directly; a writable sandbox lets Codex build and run tests. Since Codex may then edit files, run git, and reach outside `--cd`, put the limits in the prompt: which paths it may change, and no commits unless asked. Pass `--read-only` (or `--sandbox <mode>`) only when the job must not change anything.
- Pushing is blocked: the script appends a no-push rule to every task prompt and rewrites every push URL in the job's environment, so `git push` and `gh pr create` fail while fetching still works. Pass `--allow-push` (on `start`, or on a later `resume`) only when the user explicitly asked for Codex to push.
- Pick a name that says what the job is; names are global across sessions, and `start` refuses an existing one.
- `--image <file>` attaches an image (repeatable). `--model` and `--effort` override the Codex config; leave them unset unless the user asks.

Write the prompt as a brief for a capable engineer who has none of your context: the goal, where to look, constraints (what not to touch, which commands not to run), how to verify, and what the final message must contain. One task per job. For image generation, tell Codex to use its image generation tool and save the result under a given file name in the working directory.

## While it runs

```bash
python3 $J peek fix-null-deref          # last 20 entries: messages, commands with exit codes, file changes
python3 $J peek fix-null-deref --full   # untruncated messages and output tails
```

If it is heading the wrong way, interrupt and redirect. `stop` sends SIGINT: Codex records the turn as interrupted and cleans up its running command; edits already made stay on disk.

```bash
python3 $J stop fix-null-deref
python3 $J resume fix-null-deref <<'EOF'
Stop the refactor. First check what you already changed, then ...
EOF
```

## Continue

`resume` sends the next turn to the same thread, which keeps the full history and any sandbox the job was started with (`--sandbox` changes it). Run it in the background like `start` when the turn may be long. A job cannot take a new turn while one is running.

```bash
python3 $J result fix-null-deref        # final message of the latest turn
python3 $J list                         # jobs with status, turn count, thread id
```

Verify Codex's claims before relaying them: read the diff, run the tests yourself. Codex follows its own instruction files (`~/.codex/AGENTS.md`, the repo's `AGENTS.md`), so its replies may come back in the user's preferred language.

## Code review

```bash
python3 $J review review-main --cd /path/to/repo --base main </dev/null
python3 $J review review-wip --cd /path/to/repo --uncommitted <<'EOF'
Focus on error handling in the parser.
EOF
```

Exactly one of `--uncommitted`, `--base <branch>`, `--commit <sha>`. Instructions on stdin are optional; pass `</dev/null` when there are none.

## Files

Jobs live in `$CODEX_JOBS_DIR` (default `$TMPDIR/codex-jobs/<name>/`): `meta.json` (thread id, cwd, sandbox), and per turn `turn-N.jsonl` (event stream), `turn-N.last.md` (final message), `turn-N.err` (CLI log). The thread itself is a normal Codex session: the user can open it in the Codex app, and the `read-codex-session` skill exports it by thread id.

# skills

A collection of [Pionxzh](https://github.com/pionxzh)'s agent skills, based
on the practices I use every day with Claude Code and Codex.

## read-claude-session

Export a Claude Code session to Markdown and answer questions about it.
Works in Claude Code and Codex. Needs Python 3.

```bash
npx skills add pionxzh/skills -g -s read-claude-session
```

## read-codex-session

Export a Codex thread to Markdown and answer questions about it. Works in
Claude Code and Codex. Needs Python 3.

```bash
npx skills add pionxzh/skills -g -s read-codex-session
```

## search-claude-sessions

Full-text search across all your Claude Code sessions: prompts, replies and
tool calls. Works in Claude Code and Codex. Needs Python 3.

```bash
npx skills add pionxzh/skills -g -s search-claude-sessions
```

## codex-delegate

Run Codex as a supervised worker from Claude Code: start, check, stop,
resume and review jobs. Needs Python 3 and the Codex CLI.

Install it for Claude Code only. `--copy` keeps it out of
`~/.agents/skills/`, which Codex reads.

```bash
npx skills add pionxzh/skills -g -s codex-delegate -a claude-code --copy
```

## chrome-cdp

Drive a dedicated, logged-in Chrome: run JavaScript, trusted clicks and
typing, screenshots, downloads, console and network capture. Works in
Claude Code and Codex. Needs Node 22+ and Chrome.

```bash
npx skills add pionxzh/skills -g -s chrome-cdp
```

## Update

Skills install with [`npx skills`](https://www.npmjs.com/package/skills).
Update them with `npx skills update -g`.

## Development

To try an edit, install from the checkout: replace `pionxzh/skills` with its
path. A local install is a copy that `npx skills update` skips, so run `add`
again after each edit. If the edit deletes or renames a file, run
`npx skills remove -g -y <name>` first.

## Archived

[`archived/`](archived/) holds retired skills, kept for reference. They
cannot be installed.

## License

[MIT](LICENSE)

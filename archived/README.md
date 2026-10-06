# Archived skills

Retired skills, kept as their last installed versions, byte for byte.
`npx skills add -l` does not list them, because each `SKILL.md` sits one
level deeper than the active skills.

## en-plain, zh-eng (2026-10-06)

Both were optional, user-invoked style skills: `en-plain` for plain
engineering English, `zh-eng` for Traditional Chinese with English technical
terms. They were merged into the always-loaded global instructions
(`~/.claude/CLAUDE.md`, mirrored to `~/.codex/AGENTS.md`) and removed from
both agents.

Reasons:

- `en-plain` repeated most of the global instructions.
- `zh-eng` disagreed with the global instructions on which technical terms
  stay in English. The merge kept the `zh-eng` rule: only terms engineers
  actually say in English, plus tool names, APIs, commands, flags, paths,
  identifiers, and error messages.
- Neither set a default reply language, so long sessions full of English
  material drifted into English. The global instructions now default to
  Traditional Chinese for the whole session, treat English user messages as
  IME convenience rather than a request to switch, and switch only on an
  explicit request.
- A skill is one message in the conversation and can be summarized away
  when context is compacted; the global instructions load every session.
- The Codex copy of `en-plain` failed strict YAML parsing: the unquoted
  `description` contains `Core: short sentences`.

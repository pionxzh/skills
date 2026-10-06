---
name: en-plain
description: Plain engineering English for reports, explanations, and replies. Use when the user asks for output in English, or invokes /en-plain. Core: short sentences, common words, active voice, lead with the conclusion — no academic register, no hedging.
---

# Plain engineering English

After loading this skill, write all user-facing output (replies, reports,
analyses, docs) in this style.

## Structure — reader first

1. **Lead with the conclusion.** The first sentence answers the question the
   reader actually asked. Background and reasoning come after, never before.
2. **One idea per sentence. One topic per paragraph.** If a sentence needs a
   second comma, it is usually two sentences.
3. **Make it actionable.** When the reader must do something, say what to do
   in the imperative, then explain why. Not the reverse.

## Sentences — hard rules

4. **Keep sentences under ~20 words.** Long sentences are a signal to split,
   not to add commas.
5. **Active voice.** "The parser rejects the input", not "the input is
   rejected by the parser". Passive is allowed only when the actor is
   genuinely unknown or irrelevant.
6. **Verbs over nominalizations.** "decide" not "make a determination";
   "fails" not "results in a failure"; "checks" not "performs validation of".
7. **Common words.** "use" not "utilize/leverage"; "start" not "initiate";
   "enough" not "sufficient"; "look at" not "examine/delve into". Exact
   technical names stay exact — never simplify a term of art.

## Register — what to cut

8. **No hedging padding.** Cut "it's worth noting that", "importantly",
   "essentially", "arguably". If a claim is uncertain, state the uncertainty
   with a number or a condition, not an adverb.
9. **No academic connectives.** "But" not "however"; "so" not "therefore/
   consequently"; "also" not "furthermore/moreover".
10. **No decoration.** No metaphors, no "robust/seamless/comprehensive", no
    summary sentences that restate the paragraph above them.

## Example

Right:

> The build fails because `resolve_imports` runs before the alias table is
> built. Two fixes are possible. Move the pass after `build_aliases`, or
> make it lazy. Moving the pass is safer: nothing else reads aliases before
> that point, and the lazy version adds a cache we would have to invalidate.

Wrong (the register to avoid):

> It is worth noting that the build failure is fundamentally attributable to
> the sequencing of the import resolution phase, which is executed prior to
> the construction of the alias table. There are essentially two potential
> approaches that could be leveraged to comprehensively address this issue.

## Boundaries

- Code, identifiers, error messages, and quoted text stay verbatim.
- Precision beats brevity: never drop a technical qualifier that changes
  the meaning (e.g. "on the main thread", "only in strict mode").
- Long outputs drift back toward the academic register in later sections —
  after each section, re-check: are the sentences still short? Did hedging
  creep back in?

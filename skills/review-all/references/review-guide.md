# Google review rubric

Apply these dimensions when reviewing the change. Adapted from Google's
engineering practices, "What to look for in a code review"
(https://google.github.io/eng-practices/review/reviewer/looking-for.html).

The Google-rubric reviewer receives this text. The Codex review-command agent
runs Codex's native `codex review --base` command independently.

## How to use context (read this first)

Judge the change against **the system as a whole**, not the diff in isolation —
a change can be locally fine yet degrade overall code health. But context is a
**lens, not a license**: use surrounding and system context to decide whether the
*changed* lines are correct, well-designed, and well-placed. Anchor every finding
to a changed line or to something the change should have touched but didn't (e.g.
a missing test or doc for new behavior). Do not report pre-existing issues in
untouched code that this change neither caused nor was responsible for.

The bar: does this change **improve the overall code health** of the system, even
if it isn't perfect? If yes, it's generally approvable; findings should be things
that block that, or that meaningfully raise health if fixed.

## Dimensions

- **Design** — Does the change belong here? Do the interactions between pieces
  make sense, and does it integrate well with the rest of the system? Is this the
  right place for this logic? Flag over-abstraction and premature generality as
  well as missing structure.
- **Functionality** — Does the code do what it intends, and is that what's
  wanted? Think about edge cases, concurrency, error paths, and the human at the
  other end (users *and* future developers reading this). Flag behavior that is
  correct-looking but wrong.
- **Complexity** — Could this be simpler? Flag complexity that isn't pulling its
  weight: code that can't be understood quickly, or solving for needs that don't
  yet exist (speculative generality).
- **Tests** — Are there appropriate automated tests for new/changed behavior?
  Are the tests correct, useful, and likely to actually fail when the code
  breaks? Flag missing coverage for new logic and tests that can't fail.
- **Naming** — Do names clearly communicate what a thing is or does, without
  being needlessly long?
- **Comments** — Do comments explain *why* (intent, tradeoffs, non-obvious
  constraints) rather than restating *what* the code does? Flag stale or
  redundant comments. Note: clarifying-what comments can signal code that should
  be simplified instead.
- **Consistency & style** — Does the change follow the project's established
  conventions and the surrounding code? **Honor the repository's own
  `CLAUDE.md` / `AGENTS.md` and any stated conventions** — a documented
  convention that the change violates is a real finding. Absent a documented
  rule, match the surrounding code. Do not invent personal style rules.
- **Documentation** — If the change alters how the code is built, tested, or
  used, were the relevant docs/READMEs updated? Flag user-facing changes that
  leave docs stale.

## Priority labels (P0–P3)

Tag every finding with a priority. Use the same scale the rest of the toolchain
uses, so Codex's and the Google-rubric reviewer's labels line up:

- **P0** — Drop everything to fix. Blocks release, operations, or major usage.
  Reserve for universal issues that don't depend on assumptions about inputs.
- **P1** — Urgent. Should be addressed in the next cycle (e.g. a correctness or
  security bug that triggers under realistic inputs).
- **P2** — Normal. A real issue to fix eventually.
- **P3** — Low. Nice to have.

## Which issues to flag (noise control)

Flag an issue only if all of these hold:

- It meaningfully impacts correctness, performance, security, or maintainability.
- It is discrete and actionable — not vague or compound.
- It was introduced by *this* change — do not flag pre-existing issues in
  untouched code.
- The author would likely fix it once aware; it doesn't rely on unstated
  assumptions about their intent, and any downstream impact is identified, not
  speculative.

Beyond that: prioritize correctness and design over style; skip what a linter,
formatter, typechecker, or compiler would catch (assume CI runs those); skip
pedantic nitpicks. If nothing qualifies, report nothing — do not manufacture
findings to fill the review. A few well-justified findings beat a long list of
maybes.

## Good things

If the change does something notably well — a clean solution, a tricky case
handled, good tests, a real cleanup — say so briefly. It's signal, not flattery,
and it tells the author what to keep doing.

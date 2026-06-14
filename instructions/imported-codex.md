# AGENTS.md

## Rules

- Use plain English in code comments and docstrings unless otherwise specified.
- Write comments only when they add information the code cannot express. Prefer comments that explain why a decision exists, and avoid comments that merely restate what the code does. If code needs a comment to be understandable, simplify or rename it first. Comments are appropriate for complex regular expressions, algorithms, non-obvious constraints, and tradeoffs. When editing code, review nearby existing comments and TODOs, and remove or update any that are obsolete. Use docstrings and API documentation to explain purpose, usage, and behavior.
- Do not rewrite existing comments or docstrings only because they are written in Korean. Preserve them unless they are incorrect, obsolete, unclear for the current change, or the user explicitly asks to translate them.
- Use `gh` CLI for GitHub operations (PRs, issues, repos, etc.) instead of MCP OSS tools.
- When addressing PR feedback, do not treat every review or comment as an instruction to change code. First identify the current PR head, review state, author, timestamp, thread resolution, and whether the comment still applies to the current diff. Fix only valid, current, actionable findings; explicitly triage stale, resolved, superseded, speculative, or non-actionable feedback instead of blindly applying it.
- Make patches as simple as possible: solve the requested problem directly, keep changes narrowly scoped, follow existing patterns, and avoid opportunistic refactors, dependency churn, or formatting-only edits unless they are necessary for the task.
- Use structured logging (key=value fields) when the project's logger supports it. Write meaningful event messages, not values restated as strings: prefer a short event name plus structured fields over interpolating values into the message string.
- Use dependency injection for components, especially anything env-derived. Clients / sub-components take their config as required constructor arguments and validate it there (fail fast on empty/invalid). Only the composition root (the application entry point) reads environment variables; every other module receives values via injection. No env-var fallbacks inside constructors, no module-level env-derived defaults inside client packages.

---
name: implement-change
description: Execute a requested code change end-to-end with verification.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    category: github
    tags: [implementation, coding, tests, patch]
---
# Implement Change
## When to Use
- User asks for code edits, refactors, or feature additions.

## Procedure
1. Restate the requested change and assumptions.
2. Inspect relevant files and identify minimal edit scope.
3. Apply focused edits.
4. Run targeted verification (tests/lint/build when available).
5. Report what changed, what passed, and residual risks.

## Pitfalls
- Do not make unrelated changes.
- Do not skip verification when commands are available.
- Do not silently change behavior outside requested scope.

## Verification
- Modified files are listed.
- Tests/checks are reported with pass/fail.
- Behavior matches user request.

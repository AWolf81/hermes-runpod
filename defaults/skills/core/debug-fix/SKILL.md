---
name: debug-fix
description: Reproduce, isolate, and fix a bug using evidence-driven debugging.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    category: github
    tags: [debugging, bugfix, logs, root-cause]
---
# Debug Fix
## When to Use
- User reports incorrect behavior, crash, error logs, or failing tests.

## Procedure
1. Reproduce the issue and capture exact error output.
2. Isolate likely root cause in code/config.
3. Implement the smallest safe fix.
4. Re-run reproduction and targeted checks.
5. Report root cause, fix, and validation evidence.

## Pitfalls
- Do not patch without reproduction unless impossible.
- Avoid multi-change fixes that hide root cause.
- Do not claim fixed status without re-test.

## Verification
- Original failure path is retested.
- Fixed behavior is demonstrated.
- No new obvious regressions in touched path.

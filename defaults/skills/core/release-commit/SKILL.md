---
name: release-commit
description: Prepare release-ready change notes and commit metadata from current diff.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    category: github
    tags: [release, commit, changelog, git]
---
# Release Commit
## When to Use
- User asks for commit prep, release notes, or change summary for PR/ship.

## Procedure
1. Inspect git status and diff for all changed files.
2. Group changes by user-facing impact and technical impact.
3. Draft a conventional commit message (or user style).
4. Draft short release notes/changelog bullets.
5. Flag risks, migrations, and follow-up tasks.

## Pitfalls
- Do not hide breaking changes.
- Do not include unrelated changes in release notes.
- Avoid vague commit summaries.

## Verification
- Commit summary matches actual diff.
- Notes include behavior changes and ops-impacting items.
- Risks are explicit.

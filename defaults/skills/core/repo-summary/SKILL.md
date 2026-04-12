---
name: repo-summary
description: Summarize a repository from direct file evidence only; fail closed if access is missing.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    category: github
    tags: [repo, summary, architecture, codebase]
---
# Repo Summary
## When to Use
- User asks what a repo is, what it contains, or how it is structured.
- You need a concise architecture and purpose overview.

## Procedure
1. Confirm working directory and list top-level files/directories.
2. Read key docs and entrypoints (`README`, main service files, build/config files).
3. Identify runtime modes, integrations, and important env vars.
4. Collect concrete evidence: include at least 3 real file paths you actually inspected.
5. Summarize purpose, components, and workflow in concise sections.
6. Call out unknowns explicitly instead of guessing.

## Required Output Contract
- Include an `Evidence` section listing inspected file paths.
- Every major claim must map to at least one path from `Evidence`.
- If fewer than 3 files were inspectable, do not produce a normal repo summary.
- Instead output `Access issue` with:
  - what was attempted
  - what was missing (files/tools/workdir)
  - how to fix (mount path and set working directory)

## Pitfalls
- Do not invent skill names or tool names.
- Do not claim file contents you have not inspected.
- Avoid broad speculation about architecture without evidence.
- Do not write generic quality statements like "fully verified" without path evidence.

## Verification
- Summary references at least 3 actual files/paths.
- Includes current runtime/build flow and key config knobs.
- No claim exists without supporting evidence.

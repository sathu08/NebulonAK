---
name: build
version: 1.0.0
description: Implement, test and fix code in the session workspace — opencode-style build loop.
---

# Build

## Purpose

Implement approved code changes opencode-style: read, change, verify — one small step at a time.

## Inputs

* `goal`: Required build goal text.
* `plan`: Required approved plan with worker assignments from the saved plan file.
* `context`: Optional background facts or prior observations.
* `files`: Optional list of target files (must resolve inside the session).

## Requirements

* Worker must have build mode enabled.
* Harness session must exist.
* An approved plan (or explicit approval) before writing code — `nak_plan.md` status `approved` or `auto-approved`.

## Steps

1. Read the goal and the approved plan; confirm every target file is inside this session.
2. If the plan is missing or not approved, stop and report — never re-plan from scratch.
3. Inspect the relevant files (read/observe) to ground the change.
4. Implement the plan one approved step at a time via file/directory writes plus allowlisted terminal checks (`uv`, `python`, `pytest`); preserve the plan's step text and order, and never exceed its 3 steps.
5. Report files changed plus the check result; on the first failure, stop and report the exact command and error rather than guessing.

## Outputs

* `build`: Files changed plus per-step results.
* `status`: Pending-approval, auto-approved, approved or rejected.
* `files`: Files created or modified.

## Security

* Check any tool invocation **you** make against the permission engine.
* Never use a craft definition to bypass permissions.
* Only run allowlisted terminal binaries.
* Restrict file access to the session/authorized directories; never read `.env`,
  `*.key` or `secrets/`.
* Never modify `.git/`; read-only git commands (`status`, `diff`, `log`) are fine.
* Do not overwrite existing files without the required authorization.

## Failure Handling

Stop and report missing goals or plans, unapproved or unparseable plans,
out-of-session paths, the first failing check, refused writes, and a plan the
harness had to truncate (ask for it to be split).

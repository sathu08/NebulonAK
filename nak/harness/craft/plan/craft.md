---
name: plan
version: 1.0.0
description: Propose and record an approved plan within defined craft limits.
---

# Plan

## Purpose

Propose a numbered plan for an approved task and wait for approval before execution.

## Inputs

* `goal`: Required task goal text.
* `context`: Optional background facts or prior observations.

## Requirements

* Worker must have plan mode enabled.
* Harness session must exist.

## Steps

1. Read the goal and confirm every referenced path stays inside the session workspace.
2. Identify the files/inputs the plan must inspect; encode those reads as numbered steps — do not perform the reads yourself.
3. Propose a numbered plan of at most 3 steps, then stop and wait for approval.
4. Do not write `nak_plan.md` yourself: the harness records it as pending-approval (auto-approved when the step count is within the worker's `auto_steps` limit).
5. After the approval decision the harness flips the file to approved/rejected and hands off execution; if denied or no decision arrives, report the plan as pending/declined — never self-approve.

## Outputs

* `plan`: Numbered steps (at most 3; extra steps are truncated with a note).
* `status`: Pending-approval, auto-approved, approved or rejected.
* `path`: Session `nak_plan.md`, written by the harness.

## Security

* Check any tool invocation **you** make against the permission engine.
* Never use a craft definition to bypass permissions.
* Restrict file access to the session/authorized directories; never read `.env`,
  `*.key` or `secrets/`.
* Never modify `.git/`; read-only git commands (`status`, `diff`, `log`) are fine.
* Do not overwrite existing files without the required authorization.

## Failure Handling

Stop and report missing goals, out-of-session paths, unparseable plans, a plan
the harness had to truncate (ask for it to be split), and harness plan-file
failures.

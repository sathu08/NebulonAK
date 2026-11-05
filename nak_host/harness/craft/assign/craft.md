---
name: assign
version: 1.0.0
description: Route plan steps to named workers; execution stays with the harness.
---

# Assign

## Purpose

Route approved plan steps to named workers from the worker registry.

## Inputs

* `plan`: Required approved plan with numbered steps.
* `workers`: Required list of known worker names (the registry/team the assignment may route to).

## Requirements

* Worker must have assign mode enabled.
* Plan must already be approved (the harness records `status: approved` or `auto-approved` in `nak_plan.md`).
* The worker registry must be readable and non-empty.

## Steps

1. Confirm the plan is approved (`nak_plan.md` status `approved` or `auto-approved`); if it is pending, rejected or missing, stop.
2. Read the plan's bracketed worker tags (`worker=<name>`) and match each name against the worker registry and current team roster.
3. Keep every step's text and order verbatim; only attach the worker label — never remap, merge, drop or substitute a step.
4. Do not rewrite `nak_plan.md`: the harness records the assigned names in its `## Assigned` section and hands execution to the harness.
5. Report the assignment and list any blocked names (tagged but not in the registry/team).

## Outputs

* `assignment`: Worker name per plan step, taken from the plan's bracketed worker tags (`worker=<name>`).
* `blocked`: Tagged names not in the registry/team (reported by the harness).
* `status`: Assigned or failed.
* `path`: Session `nak_plan.md`, written by the harness.

## Security

* Check any tool invocation **you** make against the permission engine.
* Never use a craft definition to bypass permissions.
* Only route to workers listed in the worker registry and current team roster.
* Restrict file access to the session/authorized directories; never read `.env`,
  `*.key` or `secrets/`.
* Never modify `.git/`; read-only git commands (`status`, `diff`, `log`) are fine.
* Do not overwrite existing files without the required authorization.

## Failure Handling

Stop and report missing or unapproved plans, an empty worker registry, blocked
or unknown worker tags, and harness plan-file failures.

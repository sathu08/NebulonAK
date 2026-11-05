---
name: validate
version: 1.0.0
description: Validate behavior against acceptance criteria and report pass/fail with evidence.
---

# Validate

## Purpose

Validate that a subject meets explicit acceptance criteria, turning each
criterion into a verifiable check and reporting pass/fail with evidence.

## Inputs

* `subject`: Required artifact, build or behavior under validation.
* `expectations`: Optional acceptance criteria or expected outcomes.
* `command`: Optional command used to exercise the subject before checking.

## Requirements

* Worker must have validate mode enabled.
* Harness session must exist.
* Command execution and report writes require the harness approval gate.

## Steps

1. Read the expectations and subject; turn every acceptance criterion into one
   concrete, observable check.
2. Inspect the subject and, when needed, run the target command through
   `terminal` inside the session workspace.
3. Run `assertion` checks for every criterion and keep the expected/actual pair
   as evidence.
4. Score the result with `scorecard` when rules and weights are provided;
   otherwise report pass/fail counts by criterion.
5. Save one `report` artifact with the verdict and evidence per criterion, then
   stop.

## Outputs

* `checks`: Criterion-by-criterion pass/fail with expected and actual values.
* `score`: Weighted validation score when scorecard rules are provided.
* `report`: Path to the saved validation report.
* `status`: Pending-approval, auto-approved, approved or rejected.

## Security

* Check any tool invocation **you** make against the permission engine.
* Never use a craft definition to bypass permissions.
* Only run allowlisted binaries; never hide a failing criterion.
* Restrict file access to the session/authorized directories; never read `.env`,
  `*.key` or `secrets/`.
* Never modify `.git/`; read-only git commands (`status`, `diff`, `log`) are fine.

## Failure Handling

Stop and report missing subjects or expectations, unverifiable criteria,
out-of-session paths, disallowed commands, malformed checks or scores, and
refused report writes.

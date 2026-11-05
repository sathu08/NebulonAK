---
name: assess
version: 1.0.0
description: Evaluate a subject with assertions, trace evidence, scoring and a saved report.
---

# Assess

## Purpose

Evaluate an approved subject against explicit expectations, gather evidence for
failures, score the result with stated rules and save one report artifact.

## Inputs

* `subject`: Required artifact, claim or path under assessment.
* `expectations`: Optional checks as {id, op, expected, actual}.
* `rules`: Optional scorecard rules with explicit min/max and weights.

## Requirements

* Worker must have assess mode enabled.
* Harness session must exist.
* Paths used for evidence or reports must resolve inside the session.
* Report writes require the harness approval gate.

## Steps

1. Read the subject and expectations; confirm every path used is inside the session.
2. Run `assertion` with `action: "check"` on the expectations and record each
   pass or failure with its evidence.
3. Run `trace` with `action: "trace"` for every failure (or when asked) and keep
   the supporting lines.
4. Run `scorecard` with `action: "score"` and the stated rules, write the
   `report` artifact with `action: "write"`, and stop.

## Outputs

* `assessment`: Assertion results, trace evidence and scorecard breakdown.
* `score`: Weighted score from the scorecard rules.
* `report`: Path to the saved report artifact.
* `status`: Pending-approval, auto-approved, approved or rejected.

## Security

* Check any tool invocation **you** make against the permission engine.
* Never use a craft definition to bypass permissions.
* Restrict file access to the session/authorized directories; never read `.env`,
  `*.key` or `secrets/`.
* Never modify `.git/`; read-only git commands (`status`, `diff`, `log`) are fine.
* Do not overwrite existing files without the required authorization.

## Failure Handling

Stop and report missing subjects or expectations, out-of-session paths,
malformed checks or rules, refused or oversized reports, and harness failures.

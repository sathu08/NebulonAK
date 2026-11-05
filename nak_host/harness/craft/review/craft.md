---
name: review
version: 1.0.0
description: Review code against requirements and report defects, risks and missing tests with evidence.
---

# Review

## Purpose

Review a code change or subject against explicit requirements, identify defects
and risks, and report every finding with reproducible evidence.

## Inputs

* `subject`: Required file, directory or change under review.
* `requirements`: Optional acceptance criteria or specification to review against.
* `focus`: Optional area to prioritize (security, correctness, performance, tests).

## Requirements

* Worker must have review mode enabled.
* Harness session must exist.
* Review is read-only; writing the report requires the approval gate.

## Steps

1. Read the requirements and the subject; list the files in scope and confirm
   every path stays inside the session workspace.
2. Inspect the relevant code and its surrounding context; use `trace` to confirm
   call sites and behavior instead of assuming them.
3. Compare observed behavior against the requirements and record each finding
   with the file, evidence, impact and a concrete fix.
4. Rank findings as blocker, major, minor or nit, and list missing or weak tests
   separately.
5. Save one `report` artifact containing the ranked findings and stop.

## Outputs

* `findings`: Ranked defects and risks with file, evidence, impact and fix.
* `missing_tests`: Gaps in coverage found during review.
* `report`: Path to the saved review report.
* `status`: Pending-approval, auto-approved, approved or rejected.

## Security

* Check any tool invocation **you** make against the permission engine.
* Never use a craft definition to bypass permissions.
* Review only: do not modify code unless another approved craft grants writes.
* Restrict file access to the session/authorized directories; never read `.env`,
  `*.key` or `secrets/`.
* Never modify `.git/`; read-only git commands (`status`, `diff`, `log`) are fine.

## Failure Handling

Stop and report a missing subject, out-of-session paths, unreadable files,
requirements that cannot be verified, and refused report writes.

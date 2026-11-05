---
name: test
version: 1.0.0
description: Write focused tests, execute them and report exact pass/fail evidence.
---

# Test

## Purpose

Write and execute focused tests for an approved target, then report the exact
command, results and failure evidence without weakening the tests.

## Inputs

* `target`: Required code path, module or behavior under test.
* `criteria`: Optional expected behaviors or edge cases to cover.
* `command`: Optional test command (default `python -m pytest`).

## Requirements

* Worker must have test mode enabled.
* Harness session must exist.
* Test writes and command execution require the harness approval gate.

## Steps

1. Read the target code and existing tests; identify the behavior, inputs and
   edge cases to cover.
2. Write focused, deterministic tests inside the session workspace with `file`;
   never weaken or delete an existing test to make it pass.
3. Run the tests through `terminal` using `python -m pytest <path>` and capture
   the exact command and output.
4. Use `trace` on the failure output for each failing test and record expected
   vs actual plus the likely root cause.
5. Save one `report` artifact with the command, pass/fail counts and failure
   evidence, then stop.

## Outputs

* `tests`: Files created or updated by the run.
* `command`: Exact test command executed.
* `results`: Pass/fail counts and failure evidence.
* `report`: Path to the saved test report.
* `status`: Pending-approval, auto-approved, approved or rejected.

## Security

* Check any tool invocation **you** make against the permission engine.
* Never use a craft definition to bypass permissions.
* Only run allowlisted binaries; run tests via `python -m pytest`.
* Restrict file access to the session/authorized directories; never read `.env`,
  `*.key` or `secrets/`.
* Never modify `.git/`; read-only git commands (`status`, `diff`, `log`) are fine.

## Failure Handling

Stop and report a missing target, out-of-session paths, refuses writes, a
disallowed test command, the first failing test with its full output, and
harness or approval failures.

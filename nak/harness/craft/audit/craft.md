---
name: audit
version: 1.0.0
description: Audit code or artifacts against explicit rules, gather evidence and score the findings.
---

# Audit

## Purpose

Audit a subject against explicit rules or policies, verify each rule with
evidence, and report a scored verdict without guessing.

## Inputs

* `subject`: Required file, directory or artifact under audit.
* `rules`: Optional rules or policy statements the subject is measured against.
* `scope`: Optional paths or areas to include in the audit.

## Requirements

* Worker must have audit mode enabled.
* Harness session must exist.
* Audit is read-only except for the saved report, which requires approval.

## Steps

1. Read the rules and the subject; define one concrete check per rule and
   confirm every path stays inside the session workspace.
2. Inspect the relevant files and use `trace` to collect evidence for every
   candidate finding.
3. Run `assertion` checks for each rule that can be verified mechanically and
   keep the pass/fail evidence.
4. Score the audit with `scorecard` when rules provide weights, otherwise
   summarize findings by severity.
5. Save one `report` artifact with the rule, verdict and evidence for every
   finding, then stop.

## Outputs

* `findings`: Rule-by-rule verdicts with supporting evidence.
* `score`: Weighted audit score when scorecard rules are provided.
* `report`: Path to the saved audit report.
* `status`: Pending-approval, auto-approved, approved or rejected.

## Security

* Check any tool invocation **you** make against the permission engine.
* Never use a craft definition to bypass permissions.
* Do not modify the audited subject; report problems instead of fixing them.
* Restrict file access to the session/authorized directories; never read `.env`,
  `*.key` or `secrets/`.
* Never modify `.git/`; read-only git commands (`status`, `diff`, `log`) are fine.

## Failure Handling

Stop and report a missing subject, missing or unverifiable rules, out-of-session
paths, malformed checks or scores, and refused report writes.

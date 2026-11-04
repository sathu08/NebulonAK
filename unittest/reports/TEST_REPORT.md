# NebulonAK Worker Evaluation Report — tools / permissions / crafts

Date: 2026-10-15 (UTC) · Suite: `unittest/` · Result: **110 passed, 0 failed** (1.5s, offline, no Mind/DB needed)
Run: `uv run --with pytest python -m pytest unittest -q`

## What was built
`unittest/` (offline under `unit/` + `integration/`, live under `live/`, reports under `reports/`):
- `conftest.py` — triage-aware FakeBrain (decide prompts get valid worker/tool JSON, chat prompts get plan text), tmp workspace under `<repo>/workspace/unittest-*` (so default `workspace/**` resource grants apply), fixtures: fake_brain, harness_vizhi (+approved), harness_arivu, worker_vizhi/arivu, bare_worker
- `unit/worker/test_worker_simple.py` (12) — list/show/prompt/can/needs_approval/extract simple paths
- `unit/worker/test_worker_difficult.py` (28) — manifest fail-loud, autonomy validation/enforcement, memory on/off, triage clarify/escalate, boundary
- `unit/harness/test_tools_simple.py` (6) + `test_tools_difficult.py` (14) — file/directory/observe/terminal happy + sandbox escapes
- `unit/harness/test_permissions_simple.py` (7) + `test_permissions_difficult.py` (17) — verdicts/rule_for/inspect + fail-closed/ask-gates/resources/limits
- `unit/harness/test_crafts_simple.py` (7) + `test_crafts_difficult.py` (10) — plan/assign load/list/expand/worker mapping + fail-closed
- `integration/test_wiring_integration.py` (9) — make_worker/make_harness/propose_plan + end-to-end worker+tool+permission+craft

## How the workers evaluate

### vizhi (project_manager, modes plan+assign, crafts plan+assign)
- Prompt renders name/role/crafts, autonomy lines propose-numbered-plans-then-wait + assign tags.
- `can(plan)=True, can(assign)=True`, default approval=all → every plan waits.
- Tool boundary: file.read/list allow, file.write ask (approver True runs, False denies with need_approval), file.delete deny, terminal.exec allow, observe.read allow.
- Craft: sees plan + assign, expands plan with goal/context.
- Chat (FakeBrain): triage policy chat → answers, recalls memory, stores transcript + facts.

### arivu (researcher, modes plan, crafts plan)
- Prompt renders researcher, `can(plan)=True, can(assign)=False` (assign tags stripped in code).
- Tool boundary: file.read/list allow, file.write deny, terminal deny, observe allow — verified same-call vizhi-ask vs arivu-deny.
- Craft: sees plan only (assign filtered by allowlist + modes gate).

## Tools — verdict: healthy
- file write→read, list, directory create/list/rename, observe capture, terminal echo all pass via approved harness.
- Sandbox blocks: `../`, `/etc/passwd`, `.env`, `*.key`, `secrets/`, `.git/`, missing path, bad action, create-mode overwrite, root delete, sudo, rename-overwrite — all deny with clear errors.
- Large read truncates at 8000 chars with truncated=true.

## Permissions — verdict: fail-closed, correct
- Unknown worker/tool → deny; missing file → deny-all; disabled worker → deny.
- vizhi write without approver → ask (need_approval:true); with approver True → allow; False → deny.
- Resource deny wins (`*password*`), per-call allow=[...] intersection enforced, limits/audit/bind checks pass.
- Key finding (by design): session files only grant when workspace is under `<repo>/workspace` (relative-to-project `workspace/**` glob). Tests use that layout; tmp dirs outside the repo would deny — expected, not a bug.

## Crafts — verdict: healthy, flat-only enforced
- plan (5 steps, approval small, tools file/directory/observe) + assign (5 steps, approval all) load, list, expand, gaps={}.
- crafts_for_worker: vizhi→plan+assign, arivu→plan-only.
- Fail-closed: unknown/empty name raises, missing required inputs raises, tools all known, worker allowlist respected, `[worker=]`/ `Craft:` nesting refused.

## Wiring — verdict: healthy
- make_worker injects decider+policy, rejects bad crafts shape; make_harness lists tools.
- propose_plan: small plan auto-approved, big plan pending without approver, no-steps → planned False, policy clarify/escalate → no plan.
- End-to-end: vizhi chat→extract→approved write→craft expand succeeds; arivu same write denied.

## Difficult-case notes (fixed during build)
1. vizhi write is ask-gated — tool tests need approver harness or they hit need_approval before sandbox.
2. FakeBrain must answer triage prompts with valid worker/tool JSON or policy escalates (empty triage → escalate) and chat never reaches memory/tools.
3. Workspace must be under repo/workspace for default resource globs to grant.
4. workers.json `model` forbidden fires as bad-identity-field when nested; top-level `model` fires forbidden — both fail-loud, correct.

No code changes were needed — all 110 pass against current implementation.

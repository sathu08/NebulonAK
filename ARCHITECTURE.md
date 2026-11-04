# NebulonAgentKit — Architecture & Plan

Status: **Approved — Plan v2.1**
Scope: turn NebulonAgentKit (NAK) into a modular agent framework where a user connects to NebulonMind + NebulonDB, gives NAK a task, and NAK plans, delegates, executes, verifies and reports it.

Non-goal: this is a **layering + contracts** migration, not a rewrite. Existing manifests, tools, sandbox, permissions and the "no local LLM in NAK" rule stay unchanged.

---

## 1. Core idea

**Task is the unit of work.** Everything else exists to serve a task:

- **Brain** = LLM/memory gateway to NebulonMind.
- **Worker** = an agent identity + behavior policy.
- **Team** = a named roster of workers.
- **Craft** = a capability/mode definition (plan, build, test, review, ...).
- **Harness** = tool execution + sandbox + permissions.
- **Orchestrator** = application layer that coordinates use-cases.
- **TaskStore** = durable, resumable state machine.

"Drag and drop" is the **extension model**; fixed contracts make drops safe.

---

## 2. Architecture

Dependency direction is inward only.

```
Interfaces      CLI (nebuloncode) · HTTP API · TUI
                     │
Application     create · plan · assign · run · chain · verify · report
                orchestrator (coordinates only) · agent loop
                     │  depends on ports
Domain          Task · TaskRun · Result · Worker · Team · Craft · Tool
                Policy · Permission · ExecutionContext · errors
                     ▲  adapters implement ports
Adapters        Brain/Mind · Memory/DB · JSON registry · SQLite store
                Sandbox executor · Tools · Laya decision
                     ▲
Composition     wiring.py — builds concretes, injects them
```

Rules:

1. Domain imports nothing outward (no HTTP, no files, no Brain).
2. Application depends only on **ports** (Protocols), never concrete adapters.
3. Adapters implement ports and may import domain.
4. `wiring.py` is the only module allowed to import all layers.
5. Interfaces contain no business logic.

---

## 3. Domain contracts

### 3.1 Deep freeze

`MappingProxyType` only protects the outer mapping, so nested dicts/lists must be frozen too.

```python
from types import MappingProxyType


def deep_freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(v) for v in value)
    return value
```

Note: `MappingProxyType` is not hashable. Key tasks by `id`; add a canonical `__hash__` only if needed.

### 3.2 Task (immutable)

```python
@dataclass(frozen=True)
class Task:
    id: str
    idempotency_key: str
    goal: str
    inputs: MappingProxyType
    acceptance: tuple[str, ...]
    workspace: str
    max_steps: int
    timeout: int
    allowed_tools: frozenset[str]

    def __post_init__(self):
        object.__setattr__(self, "inputs", deep_freeze(self.inputs))
        object.__setattr__(self, "acceptance", tuple(self.acceptance))
        object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))
```

### 3.3 TaskRun (mutable execution state)

```python
@dataclass
class TaskRun:
    task_id: str
    state: TaskState
    version: int                       # optimistic concurrency
    assigned_worker: str | None
    attempt: int
    block_reason: BlockReason | None = None
    result: Result | None = None
```

Only `TaskStore.transition()` mutates a run; callers receive snapshots.

### 3.4 Enums

```python
class TaskState(str, Enum):
    CREATED = "created"
    PLANNED = "planned"
    APPROVED = "approved"
    ASSIGNED = "assigned"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"
    VERIFIED = "verified"
    REPORTED = "reported"
    FAILED = "failed"
    BLOCKED = "blocked"


class ResultStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_APPROVAL = "needs_approval"
    CANCELLED = "cancelled"
```

### 3.5 Result

```python
@dataclass(frozen=True)
class Result:
    task_id: str
    worker: str
    status: ResultStatus
    summary: str
    files_changed: tuple[str, ...]
    commands_run: tuple[str, ...]
    evidence: MappingProxyType
    blockers: tuple[str, ...]
    needs_approval: bool
```

### 3.6 Block / recovery metadata

```python
@dataclass(frozen=True)
class BlockReason:
    code: str            # interrupted | permission_denied | approval_required | policy | crash
    detail: str
    resumable: bool
    recovery: MappingProxyType   # last_step, checkpoint, worker, session_id
```

`RUNNING -> BLOCKED(code="interrupted")` on startup recovery is resumable only when a checkpoint exists. An ordinary permission block uses `code="permission_denied", resumable=False`. The application therefore knows whether it can safely resume.

---

## 4. Ports and execution boundary

```python
class Verdict(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionPolicy(Protocol):
    def verdict(self, worker: str, tool: str, capability: str,
                resource: str | None, ctx: "ExecutionContext") -> Verdict: ...


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...
    def raise_if_cancelled(self) -> None: ...


@dataclass(frozen=True)
class ApprovalRef:
    id: str
    scope: str                 # task | step | tool
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None = None
    revoked: bool = False


class ApprovalAuthority(Protocol):
    def is_valid(self, ref: ApprovalRef | None, ctx: "ExecutionContext") -> bool: ...


@dataclass(frozen=True)
class ExecutionContext:
    task_id: str
    worker_id: str
    workspace: str
    allowed_tools: frozenset[str]
    attempt: int
    approval: ApprovalRef | None
    policy: PermissionPolicy
    authority: ApprovalAuthority
    cancel: CancellationToken


class ToolExecutorPort(Protocol):
    def inspect(self, tool: str, args: dict, ctx: ExecutionContext) -> Verdict: ...
    def run(self, tool: str, args: dict, ctx: ExecutionContext) -> "ToolResult": ...


class BrainPort(Protocol):
    def chat(self, text: str, **kw) -> dict: ...
    def search(self, **kw) -> dict: ...


class RegistryPort(Protocol):
    def workers(self) -> list: ...
    def teams(self) -> list: ...
    def crafts(self) -> list: ...
    def tools(self) -> list: ...
    def permissions(self) -> object: ...


class MemoryPort(Protocol):
    ...


class ApproverPort(Protocol):
    def __call__(self, info: dict) -> bool: ...
```

Security requirements:

1. `inspect()` and `run()` both take `ExecutionContext`.
2. `run()` re-checks `authority.is_valid(...)`, `cancel.cancelled`, workspace boundary and allowed tools **at execution time** (not only at inspection time).
3. `ExecutionContext` is constructed only in the application layer, so a worker cannot forge scope.
4. Approval revocation is respected mid-run because the authority, not the boolean ref, is consulted.

---

## 5. TaskStore (NebulonDB only, append-only, credentials from Mind)

The TaskStore lives in **NebulonDB itself** — one store, no SQLite. NAK
configures only its Mind connection; the DB host/username/password resolve
from Mind's gated `GET /api/NebulonMind/system/db_connection` endpoint
(bearer-shared-secret only; refused when Mind auth is disabled), cached in
memory with TTL and refreshed on NDB 401. Explicit per-field overrides
(kwarg > env > cfg) always win; `resolve_from_mind=false` restores legacy
direct configuration. One corpus, `cosmos` type (plain JSON, no vectors):

```
corpus: nak_tasks  (cosmos)
├── tasks         one doc per task               (insert once)
├── task_runs     one snapshot doc per transition (current = max version)
├── task_events   one doc per event               (event_id = "{task_id}:{seq}")
└── task_results  one doc per attempt             (latest attempt wins)
```

Verified against NebulonDB's routes: for `cosmos` segments, `add_records`
(insert), `load_segment` (bulk insert), `get_data` (read), and
`delete_records` work, but `update_records` is broken for cosmos (it calls
`update_vec`, which `ComosDBManager` does not have) and `get_record`
explicitly rejects cosmos. The design therefore uses **inserts + reads only —
no update endpoint is ever called**.

```python
class TaskStorePort(Protocol):
    def create_task(self, task: Task) -> TaskRun: ...
    def get_task(self, task_id: str) -> tuple[Task, TaskRun] | None: ...
    def get_task_by_idempotency(self, key: str) -> tuple[Task, TaskRun] | None: ...
    def transition(self, task_id: str, expected_version: int,
                   to_state: TaskState, event: dict,
                   **fields) -> TaskRun: ...   # event-first + idempotent replay
    def append_event(self, task_id: str, event: dict) -> None: ...
    def save_result(self, task_id: str, result: Result) -> None: ...
    def list_pending(self) -> list[TaskRun]: ...
    def recover_incomplete(self) -> list[TaskRun]: ...
```

Guarantees (implemented in the adapter, since the server has no
transactions, CAS, or unique constraints):

- **Event-first ordering + idempotent replay.** `transition()` inserts the
  event doc first (with a deterministic `event_id`), then the run snapshot.
  A crash between the two is healed by replay: recovery rebuilds any snapshot
  the events imply. Correctness never depends on two writes landing together.
- **Optimistic concurrency.** Read-check-insert under the adapter's
  single-writer lock: `version` mismatch raises `ConcurrencyError`.
- **Idempotency.** `idempotency_key` is checked under the same lock before
  insert; deterministic `event_id`s make retries safe to re-issue.
- **Single-writer rule.** Exactly one TaskStore instance owns all writes (CLI
  and API both go through it). Concurrent writer *processes* are unsupported
  in v1 (documented; a lease design is reserved for later).
- **Doc shape.** All metadata values are JSON scalars; nested structures
  travel as JSON-string fields (bulletproof against storage encoding).
  Reserved `add_records` keys (`text`, `lang`, `type`, `created_at`, `label`,
  `id`, `_id`) are never used as field names — app keys are `task_id`,
  `event_id`, `kind`, etc. Each doc carries a human-readable `text` summary
  so records are legible in the DB dashboard.
- `recover_incomplete()` marks stale `RUNNING` runs as
  `BLOCKED(interrupted)` with structured recovery metadata.

---

## 6. Task state machine

```
CREATED        -> PLANNED | FAILED
PLANNED        -> APPROVED | NEEDS_APPROVAL | FAILED
NEEDS_APPROVAL -> APPROVED | FAILED            (rejected)
APPROVED       -> ASSIGNED | FAILED
ASSIGNED       -> RUNNING | FAILED
RUNNING        -> NEEDS_APPROVAL | VERIFIED | BLOCKED | FAILED
BLOCKED        -> RUNNING (resume) | FAILED     (terminal)
VERIFIED       -> REPORTED | FAILED
REPORTED       -> terminal
FAILED         -> terminal
```

- Terminal states: `REPORTED`, `FAILED`.
- Resumable: `BLOCKED` only when `block_reason.resumable` is true.
- Retries create a new attempt (`attempt += 1`); they never reverse a transition.

---

## 7. Application use-cases

No giant orchestrator. `orchestrator.py` only coordinates the use-cases below.

```
nak/application/
├── create_task.py      # validate, dedupe by idempotency key, persist
├── plan_task.py        # plan craft -> steps -> approval gate
├── assign_task.py      # auto-assign OR explicit worker call
├── run_task.py         # build ExecutionContext, run one worker loop
├── run_chain.py        # e.g. arivu -> ezhilan (+ bounded fix loop)
├── verify_task.py      # tests/review evidence
├── approve_task.py     # human/approver decision
├── report_task.py      # diff + report artifact
├── orchestrator.py     # coordinates only
└── loop.py             # agent loop: brain -> tool -> observe -> repeat
```

Explicit worker calls:

```bash
nebuloncode call arivu --task "write log_analyzer.py + cli.py"
nebuloncode call ezhilan --task "run the required tests" --from <arivu_task_id>
nebuloncode chain --goal "build and verify log analyzer" \
  --steps arivu:write,ezhilan:test,thiran:review
```

Workspace is shared; context is isolated. Handoff is an explicit `Result`, not shared memory.

---

## 8. Target layout (incremental, backward compatible)

```
nak/
  domain/        task.py worker.py team.py craft.py tool.py
                 policy.py permission.py ports.py state.py errors.py
  application/   the use-cases above + loop.py
  adapters/      brain_nebulon.py memory_nebulon.py registry_json.py
                 store_nebulondb.py store_memory.py decision_laya.py tools/
  interfaces/    cli/ (nebuloncode) · api/
  config/        settings.py
  wiring.py      composition root only
```

Existing packages (`nak/brain`, `nak/worker`, `nak/harness`, `nak/decision`, `nak/api`) stay in place and become adapters/domain behind shims. `wiring.run_craft` and `propose_plan` remain as facades.

---

## 9. Extension points (drag and drop)

| To add... | Drop in | Core edit |
|---|---|---|
| Agent | `workers.json` + `prompts/<name>.md` + `permissions.json` entry | none |
| Team | `teams.json` | none |
| Capability mode | `crafts.json` + `craft/<mode>/craft.md` | none |
| Tool | `tools.json` + module with matching function | none |
| Brain/DB backend | new adapter implementing the port + one line in `wiring.py` | wiring only |
| Model/provider | NebulonMD config only (never in NAK) | none |

---

## 10. Migration phases

Every phase must preserve backward compatibility and pass the existing `unittest/` suite before the next phase begins.

| Phase | Priority | Deliverable | Gate |
|---|---|---|---|
| A | Critical | `nak/domain/` models, enums, ports, `ExecutionContext`, `deep_freeze`, `ResultStatus`, `BlockReason`, state-transition validator, manifest schemas | existing tests green |
| B | Critical | `TaskStorePort` + NebulonDB append-only adapter (+ in-memory default for tests/offline); `create_task` / `plan_task` / `assign_task`; `run_craft` becomes a facade; event-first transitions + idempotency + replay recovery | existing + store tests green |
| C | High | Registry/memory/harness adapters behind ports (no rewrite of the 28 KB engine yet); typed permission/approval adapters; execution-time re-checks | existing tests green |
| D | High | `application/loop.py` agent loop + tool executor using `ExecutionContext`; `call_worker` / `run_chain` | arivu -> ezhilan integration green |
| E | High | `nebuloncode` CLI + API share the use-cases; approval/diff UX; revocation visible mid-run | CLI/API parity tests |
| F | Critical | import-linter/ruff boundary rules; transition/recovery/contract tests in CI | CI gate |

Do **not** start by splitting the 28 KB harness engine or rewriting worker memory. Establish contracts first, then extract existing functionality behind them.

---

## 11. Testing requirements

- **Domain:** frozen `Task` (deep-frozen inputs), legal/illegal state transitions, no IO imports.
- **Ports:** contract tests run against fakes and real adapters.
- **TaskStore:** idempotent create, stale `version` rejected, state+event rollback on failure, crash recovery, resume approval, concurrent updates, event replay.
- **ExecutionContext:** out-of-workspace denied, disallowed tool denied, revoked approval denied mid-run, cancellation honored.
- **Integration:** `arivu -> ezhilan` on `tasks/log_analyzer.md`; restart mid-run; duplicate submission; permission-denial path.
- **Boundaries:** import-linter rules — "domain imports no adapters/interfaces" and "only wiring constructs adapters".

Required checks: `pytest -q` and `ruff check .` green after every phase.

---

## 12. What stays unchanged

- `nak/manifests/*.json` (workers, teams, crafts, tools, permissions).
- Sandbox and permission model.
- Crafts and craft markdown.
- NebulonMind HTTP boundary and the "no local LLM in NAK" rule.
- `make_brain`, `make_worker`, `make_harness` factories.

---

## 13. Current mapping

| Concern | Current location | Target layer |
|---|---|---|
| Brain client | `nak/brain/client.py` | Adapter (BrainPort) |
| Decision/policy | `nak/decision/` | Domain policy + Laya adapter |
| Worker entity | `nak/worker/engine/worker.py` | Domain |
| Worker memory/registry IO | `nak/worker/engine/memory.py`, `registry.py` | Split -> Adapters |
| Tools/sandbox/permissions | `nak/harness/` | Domain policy + Tool adapters |
| Plan/assign/dispatch | `nak/wiring.py:417`, `dispatch.py` | Application |
| Team roster | `nak/worker/engine/team.py` | Domain |
| API | `nak/api/` | Interface |
| CLI | `nak/worker/__main__.py` | Interface |
| `nebuloncode` | declared in `pyproject.toml:25`, missing | Interface |
| Config | `nebulonak.cfg` + `nak/utils/settings.py` | Config |
| Manifests | `nak/manifests/*.json` | Config/Registry adapter |

---

## 14. Status

**Phase A is done** (`nak/domain/` + 10 contract tests, full suite green).

**Phase B is done** (NebulonDB-only, append-only):

- `nak/adapters/store_nebulondb.py` — inserts + reads only (never the broken
  cosmos `update_records`); event-first + idempotent replay; single-writer
  lock; crash/call healing; replay recovery;
- `nak/adapters/store_memory.py` — same port, in-memory default for
  tests/offline and `run_craft`'s default store;
- `Settings` (+ optional `[nebulonmind]`/`[nebulondb]` overrides)
  + `wiring.make_task_store("memory" | "nebulondb")` (the `[nebulondb]`
  section was removed — all defaults; only re-add it to override);
- `nak/application/` use-cases (`create_task`, `plan_task`, `assign_task`)
  with injected (duck-typed) planner/harness/routing;
- `wiring.run_craft` is a facade over the use-cases with an identical return
  shape, plus `RUNNING` transition, saved `Result`, and dispatch event.
- Gate met: full suite 423 passed / 2 skipped (live NDB test skips without
  a DB); new files ruff-clean except one intentional SIM105 kept byte-identical
  to the legacy approval block.
- **Credentials from Mind (done):** `GET /api/NebulonMind/system/db_connection`
  (NebulonMD, bearer-gated, refused when Mind auth is off, audit-logged) +
  `nak/adapters/credentials_mind.py` (memory-only TTL cache) +
  401-refresh in the store/client + `resolve_from_mind` flag. Chain of trust:
  DB password rests only in Mind's `.env`; NAK holds it in memory.

Next: **Phase C** — registry/memory/harness adapters behind ports (no engine
rewrite yet).

**Phase C is done** (seams, not rewrites — full suite 451 passed / 2 skipped):

- `nak/adapters/registry_json.py` — `RegistryPort` over existing loaders;
  mtime-checked shared cache (deep-copied), `refresh()`, fail-loud entry
  shape for teams; `wiring._workers_loader`/`_tools_loader` delegate to it;
- `nak/adapters/memory_nebulon.py` — `MemoryPort` over injected brain
  (worker mixin untouched; only new code uses it);
- `nak/adapters/executor_harness.py` — `ToolExecutorPort` over
  `ExecutionHarness`: inspect→Verdict, run→ToolResult, authority-bound
  approver (TOCTOU-safe), revocation/cancel honored, ASK/DENY as results;
- `make_registry()` / `make_memory()` / `make_executor()` factories.
- Deferred to Phase D per YAGNI (no consumer yet): `decision_laya` adapter.

Next: **Phase D** — agent loop + `run_task`/`run_chain`/`call_worker`.

**Phase D is done** (full suite 477 passed / 2 skipped; one live-timing
flake passes on retry; new files ruff-clean):

- `nak/application/approval.py` — mint/grant/revoke/expiry/scope ledger;
- `nak/application/loop.py` — ReAct via prompt+parse (`raw=True`, no
  `system` role), arg schemas + worked example in the prompt, reason-bearing
  observations, honesty-check turn, stall/timeout/cancel budgets;
- `nak/application/run_task.py` — ASSIGNED->RUNNING, ctx build, Result with
  extracted files/commands evidence;
- `nak/application/run_chain.py` — child tasks + handoff + bounded fix loop
  with failed-step re-verification (supersede semantics);
- `nak/application/verify_task.py` — test/review step + VERIFIED transition;
- `nak/application/call.py` + `wiring.call_worker` — depth-1 named calls.
- State machine: `CREATED->ASSIGNED` added (chain children/named calls are
  assigned slices of a planned parent).
- **Live-verified**: real LLM + harness loop wrote, read and executed code
  (`hi` confirmed) with approval grants; hallucination guardrails held
  (honest stall/FAILED, no false success).
- **Deferred (explicit): Phase E (CLI/API) and Phase F (enforcement).**
  Python-level connection is the supported surface; if a CLI is ever needed,
  it goes in `pipeline/` (the reserved `nebuloncode` entry point).

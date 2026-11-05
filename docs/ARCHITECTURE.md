# NebulonAgentKit — Architecture

NebulonAgentKit (NAK) is a modular agent framework where a user connects to
NebulonMind + NebulonDB, gives NAK a task, and NAK plans, delegates,
executes, verifies and reports it.

Non-goal: NAK never performs LLM inference itself. Every model call goes via
`nak_host.brain` → NebulonMD HTTP API. Manifests, tools, sandbox, permissions and
the "no local LLM in NAK" rule are structural invariants, not migration
steps.

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
Interfaces      nak-worker CLI · server CLI (nebulonak.py) · pipeline CLIs (vetting)
                HTTP API (laya decision + console dashboard + static web)
                      │
Application     create · plan · assign · run · chain · verify · approve · report
                approval ledger · agent loop · orchestrator (coordinates only)
                      │  depends on ports
Domain          Task · TaskRun · Result · Worker · Team · Craft · Tool
                Policy · Permission · ExecutionContext · errors
                      ▲  adapters implement ports
Adapters        Brain/Mind · Memory/DB · JSON registry · SQLite store
                Sandbox executor · Tools · Laya decision · Mind credentials
                      ▲
Composition     wiring.py — builds concretes, injects them
                nak_host/utils/settings.py (cfg + env) · nak_host/utils/constant.py (values)
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

`inputs` is a free-form frozen map. Loop budgets (`max_calls_per_turn`,
`obs_chars`, `history_keep`, `max_stalls`, `auto_steps`) travel here and
resolve as explicit kwarg > `task.inputs` > built-in default, so callers
stay backward compatible.

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

`evidence` carries the loop answer (truncated), tool-call counts, the
failure reason, and `diff_stat` (first `terminal git diff` output) when a
diff ran.

### 3.6 Block / recovery metadata

```python
@dataclass(frozen=True)
class BlockReason:
    code: str            # interrupted | permission_denied | approval_required | policy | crash
    detail: str
    resumable: bool
    recovery: MappingProxyType   # last_step, checkpoint, worker, session_id
```

`RUNNING -> BLOCKED(code="interrupted")` on startup recovery is resumable
only when a checkpoint exists. An ordinary permission block uses
`code="permission_denied", resumable=False`. The application therefore knows
whether it can safely resume.

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

## 5. TaskStore (NebulonDB, append-only; credentials from Mind)

The production TaskStore lives in **NebulonDB itself** — one store.
`wiring.make_prod_store()` builds it; `wiring.make_task_store("memory")`
returns the process-local in-memory port implementation used for unit tests
and offline runs (`NAK_STORE=memory` selects it; anything else in prod code
resolves the prod store and fails loud when Mind/DB are down — persistence
loss is never silent). NAK configures only its Mind connection; the DB
host/username/password resolve from Mind's gated
`GET /api/NebulonMind/system/db_connection` endpoint (bearer-shared-secret
only; refused when Mind auth is disabled), cached in memory with TTL and
refreshed on NDB 401. Explicit per-field overrides (kwarg > env > cfg)
always win; `resolve_from_mind=false` restores legacy direct configuration.
One corpus, `cosmos` type (plain JSON, no vectors):

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
- **Single-writer rule.** Exactly one TaskStore instance owns all writes.
  Concurrent writer *processes* are unsupported in v1 (documented; a lease
  design is reserved for later).
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
CREATED        -> ASSIGNED                     (chain children / named calls:
                                                assigned slices of a planned parent)
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

`nak_host/application/` holds one module per use-case plus the shared agent loop:

```
nak_host/application/
├── create_task.py      # validate, dedupe by idempotency key, persist
├── plan_task.py        # plan craft -> steps -> approval gate
├── assign_task.py      # auto-assign OR explicit worker call
├── run_task.py         # build ExecutionContext, run one worker loop
├── run_chain.py        # e.g. arivu -> ezhilan (+ bounded fix loop)
├── verify_task.py      # test/review evidence
├── call.py             # depth-1 named worker call (no further delegation)
├── loop.py             # agent loop: brain -> tool -> observe -> repeat
└── approval.py         # mint/grant/revoke/expiry/scope ledger
```

The agent loop (`loop.py`) is ReAct through prompt + parse: Mind's chat
returns text (no native tool calls), so tools are offered in the prompt and
called back as fenced JSON blocks. Every Brain call uses `raw=True` (the
project's own prompt protocol) with a `TypeError` fallback for older
brains; history uses user/assistant/tool roles only (Mind rejects `system` —
the identity block goes in as `tool`). Budgets (`max_calls_per_turn`,
`obs_chars`, `history_keep`, `max_stalls`, `auto_steps`) resolve as explicit
kwarg > `task.inputs` > built-in default. An optional `on_event` callback
fires after every tool call for live streaming; failures in the callback
never break the loop. Task-scoped auto-grant covers the first `auto_steps`
`ASK` verdicts via the ledger; the rest go through the approver, and the
authority is re-consulted at execution time.

Named calls and chains (Python API — the supported surface):

```python
from nak_host.wiring import call_worker, make_prod_store, make_registry

store = make_prod_store()
call_worker("arivu", "write log_analyzer.py", store=store,
            registry=make_registry(), approver=lambda info: True)
```

```bash
# One-shot craft from the CLI: plan -> write nak_plan.md -> approve ->
# assigned workers run as peers in threads
python -m nak_host.worker --worker vizhi --craft "write log_analyzer.py" --approve
python -m nak_host.worker --team-name core --craft "build and verify log analyzer"

# Serve the API + web console; observe status, pipeline and settings
python nebulonak.py start
# open http://127.0.0.1:8000/api/NebulonAK/dashboard/
```

Workspace is shared; context is isolated. Handoff is an explicit `Result`, not shared memory.

---

## 8. Layout

```
nak_host/
  domain/        task.py worker.py team.py craft.py tool.py
                 policy.py permission.py ports.py state.py errors.py
  application/   the use-cases above + loop.py
  adapters/      brain_nebulon.py memory_nebulon.py registry_json.py
                 store_nebulondb.py store_memory.py credentials_mind.py
                 executor_harness.py
  harness/       engine/ (execution, sandbox, permission, approval,
                 capability, registry, limits) · tools/ · craft/ · ml/ · mcp/
  worker/        engine/ (worker, identity, prompt, chat, session, team,
                 dispatch, triage, autonomy, registry, memory)
   decision/      decider + policy + batch
   brain/         HTTP client over NebulonMD (the only LLM path)
   api/           laya decision endpoint + console dashboard
                  (routes/status|pipeline|settings_view|dashboard + web/ console)
   cli/           server ops: start|stop|restart|status (no TUI)
   manifests/     workers.json teams.json crafts.json tools.json permissions.json
   utils/         settings.py (cfg + env) · constant.py (all fallback values)
   wiring.py      composition root only
nebulonak.py     server entry point (mirrors nebulondb.py / nebulonmd.py)
nebulonagentkit.py  foreground API runner (same app, no daemon)
pipeline/
  vetting/       worker evaluation CLIs + runners (nak-vetting)
unittest/        unit · integration · live · server
```

`wiring.py` exposes `make_brain / make_harness / make_worker / make_registry /
make_memory / make_executor / make_task_store / make_prod_store /
call_worker / run_craft / decide`. `run_craft` is a facade over the
application use-cases with an unchanged return shape, plus `RUNNING`
transition, saved `Result`, and dispatch event.

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

## 10. Testing requirements

- **Domain:** frozen `Task` (deep-frozen inputs), legal/illegal state transitions, no IO imports.
- **Ports:** contract tests run against fakes and real adapters.
- **TaskStore:** idempotent create, stale `version` rejected, state+event rollback on failure, crash recovery, resume approval, concurrent updates, event replay.
- **ExecutionContext:** out-of-workspace denied, disallowed tool denied, revoked approval denied mid-run, cancellation honored.
- **Approval:** first-N auto-grant, approver fallback, revocation mid-run, `step_finished` streaming events.
- **Integration:** `arivu -> ezhilan` chain; restart mid-run; duplicate submission; permission-denial path.
- **Boundaries:** import-linter/ruff rules — "domain imports no adapters/interfaces" and "only wiring constructs adapters".

Required checks: `pytest -q` and `ruff check .` green. Live tests skip
without a DB; with one, Brain connectivity, prod-store round-trip and the
laya decision endpoint are exercised.

---

## 11. Invariants (never change these)

- `nak_host/manifests/*.json` (workers, teams, crafts, tools, permissions).
- Sandbox and permission model (fail-closed; deny-lists hold in any workspace).
- Crafts and craft markdown.
- NebulonMind HTTP boundary and the "no local LLM in NAK" rule.
- `make_brain`, `make_worker`, `make_harness` factories.
- `InMemoryTaskStore` is test/offline-only; prod runs use `make_prod_store()`.

---

## 12. Module map

| Concern | Location | Layer |
|---|---|---|
| Brain client | `nak_host/brain/client.py` | Adapter (BrainPort) |
| Decision/policy | `nak_host/decision/` | Domain policy + Laya adapter |
| Worker entity | `nak_host/worker/engine/worker.py` | Domain |
| Worker memory/registry IO | `nak_host/worker/engine/memory.py`, `registry.py` | Split: domain shape + adapters |
| Tools/sandbox/permissions | `nak_host/harness/` | Domain policy + Tool adapters |
| Plan/assign/dispatch | `nak_host/wiring.py`, `dispatch.py` | Application |
| Team roster | `nak_host/worker/engine/team.py` | Domain |
| API | `nak_host/api/` (laya + console routes + `web/`) | Interface |
| Worker CLI | `nak_host/worker/__main__.py` | Interface |
| Server CLI | `nak_host/cli/` + `nebulonak.py` (start/stop/restart/status) | Interface |
| Vetting pipeline | `pipeline/vetting/` (`nak-vetting`) | Interface |
| Config | `nebulonak.cfg` (`[paths]/[server]/[decision]/…`) + `nak_host/utils/settings.py` | Config |
| Shared values | `nak_host/utils/constant.py` (single source) | Config |
| Manifests | `nak_host/manifests/*.json` | Config/Registry adapter |

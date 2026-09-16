# NebulonAK → Harness Conversion Plan

> Source: `.md` (harness proposal, 992 lines) + codebase verification
> (`nak/`, `pipeline/chatagent/`, `nebulonak.cfg`, offline tests).
> Status legend: ✅ DONE · 🟡 STARTED · ⬜ NOT STARTED

## 0. Verdict

**Convert NebulonAK into a harness; do not start a new project.**
Current code is already a good agent framework — the missing piece is the
**harness/runtime as the central execution layer** (lifecycle + state +
permissions + observation + verification + recovery), not more agents.

```
NebulonAK v0.1  Agent + Tool Framework        (where we were)
NebulonAK v0.2  Agent Runtime                 (Phase 1 ✅)
NebulonAK v0.3  Execution Harness             (Phase 2 ✅)
NebulonAK v0.4  Self-correcting Agentic Harness (Phases 3 ✅ 4 ✅ 5 ✅)
NebulonAK v1.0  General-purpose Nebulon Agent Harness (Phase 6 ✅)
```

`NebulonMind` stays underneath everything (LLM + memory). No external keys.

---

## 1. Target architecture

```
User ──► NEBULON HARNESS (Runtime: plan/decide/execute/observe/verify/recover)
                    │
        ┌───────────┼───────────┐
        ↓           ↓           ↓
     Agents       Tools      Policies
        │           │           │
        └───────────┼───────────┘
                    ↓
              Observer → Verifier → Recovery
                    ↓
              NebulonMind (LLM + Memory) → NebulonDB
```

Responsibilities:

| Layer | Owns |
|---|---|
| Agent | reasoning, task behaviour, tool choice, plans/results |
| Tool | one concrete operation |
| Harness | lifecycle, state, permissions, execution, observation, verification, retry, recovery, sessions, workspace, logging |
| NebulonMind | LLM, memory, context, semantic retrieval, chat |

---

## 2. Component map (current → harness)

| Current NebulonAK | Harness role | Status |
|---|---|---|
| `Polaris` | Router / planner (called by harness, does not run tasks) | ✅ kept, demoted under runtime |
| `Genesis` | Dynamic agent factory (confirm-gated) | ✅ kept as-is |
| `Kepler` | Planning worker (+ interim recovery planner) | ✅ kept |
| `ResearchAgent` | Research worker | ✅ kept |
| `plugins` (memory + file) | Tool system | ✅ extended (fs + exec) |
| `policy.py` | Permission/safety layer | ✅ extended (per-state approvals) |
| `agent_registry.py` | Agent registry (JSON leader) | ✅ kept |
| `Brain` | LLM + memory adapter (Mind-only) | ✅ kept |
| `pipeline/chatagent/pipeline.py` | Harness prototype → thin CLI over runtime | ✅ converted |
| `prompts/` | Harness/agent instructions | ✅ kept (extend in Phase 5+) |
| tests | Harness verification | ✅ decision (18) + config (5) + phases + recovery/orchestration (28) + chatagent planning (8), all green |
| observer / verifier | Harness judges work, not the LLM | ✅ built (Phases 3+4) |
| recovery | Bounded fix loop (retry/replan/escalate) | ✅ built (Phase 5) |
| specialists + delegate/plan tools | Harness coordinates agents | ✅ built (Phase 6) |

---

## 3. Phase 1 — Runtime skeleton ✅ DONE

**Goal:** harness owns the lifecycle; explicit `HarnessState`; pipeline becomes thin wrapper.

Built:

```
nak/harness/
    __init__.py       re-exports state/executor/registry/runtime
    state.py          HarnessState, ToolCallRecord, Observation,
                      create_state(), STATUS_* (running/needs_confirm/
                      waiting_user/done/failed), to_dict/from_dict
    executor.py       AgentExecutor (is_resolvable/run_text, records agent
                      steps into state) + ToolExecutor (per-state policy gate,
                      workspace scoping, state recording)
    runtime.py        HarnessRuntime: create_state (+session +workspace),
                      decide() (stateless, BrainError→ASK_USER),
                      handle() (ASK/CREATE/USE contract + state persist to
                      workspace/state.json), observe()/should_verify()/
                      should_recover() stubs, create_confirmed/
                      list_agents_str/reset_session/is_agent_resolvable/
                      run_agent_text/handle_sync compat
```

Changed:

```
pipeline/chatagent/pipeline.py   rewritten as thin wrapper over HarnessRuntime
                                 (same public API: handle/run_agent_text/
                                 create_confirmed/list_agents_str/reset_session,
                                  brain/registry/decision/creator/session_id mirrors)
nak/plugins/tool/policy.py       added approve_tool_use_for_state(state,...)
                                 (per-run allow_once window; globals kept for compat)
```

Contract preserved: `USE_AGENT` runs only if resolvable else create-proposal;
`CREATE_AGENT` never auto-runs (proposal + `needs_confirm=True` unless
`auto_create`); `ASK_USER` returns `decision.reason` as question; every result
carries `run_on`/`routed_via`/`steps` PLUS new `state` dict.

Verified: existing `test_decision_agent` 15/15 green; runtime ASK/CREATE/USE
paths green offline (FakeBrain); `state.json` persisted per turn.

---

## 4. Phase 2 — Execution ✅ DONE

**Goal:** harness can actually operate on projects (files + shell), isolated per task.

Built:

```
nak/plugins/tool/workspace.py   get_workspace_root() (env NAK_WORKSPACE wins,
                                else <repo>/workspace), ensure_session_workspace(),
                                session_dir(), resolve_in_workspace() (.. escapes
                                rejected), sanitise_session_id()
                                layout: workspace/session_<id>/{project,logs,artifacts,state.json}
nak/plugins/tool/fs_tools.py    edit_file / list_files / search_files (+FS_TOOLS
                                schemas + execute_fs_tool, loop-safe JSON errors)
nak/plugins/tool/exec_tools.py  shell / python_exec / run_test (+EXEC_TOOLS schemas
                                + execute_exec_tool; timeout 1-120s, output tail
                                truncated to 12k, run_test wraps pytest + summary)
nak/harness/tool_registry.py    ToolRegistry(brain, workspace_root): specs()/names()/
                                execute(state,...) (delegates to ToolExecutor)
```

Changed:

```
nak/plugins/tool/__init__.py    exports FS_TOOLS/EXEC_TOOLS + executors
nak/plugins/__init__.py         PLUGIN_TOOLS = memory(5) + file(2) + fs(3) + exec(4, incl. run_build)
                                + agent/orchestration(3) = 17
                                (+ EXTENDED_TOOLS/HARNESS_TOOLS aliases);
                                execute_plugin(..., _root, _cwd) routes file/fs/exec/agent,
                                injects workspace root/cwd without changing LLM schemas
```

Policy: approval still enforced in `ToolExecutor`/`ToolRegistry` (not inside
tool functions); `shell/python/run_test` assume approval was granted.

Verified: `list/search/edit/shell/python/run_test` green in temp workspace;
`Kepler` end-to-end USE path calls new `list_files` and records it in
`state.tool_calls`; `ToolExecutor` workspace-scoped shell green.

---

## 5. Phase 3 — Observation ✅ DONE

**Goal:** every tool result classified so the harness (not the LLM) decides what happens next.

Built:

```
nak/harness/observer.py         inspect(result) -> SUCCESS | FAILURE | TIMEOUT |
                                PARTIAL_SUCCESS | NEEDS_USER (pure, offline-testable;
                                parses returncode/timeout/error markers, pytest
                                verdict lines, denial markers; never raises)
nak/harness/events.py           log_event(state, kind, summary, classification):
                                in-state observation + append to
                                workspace/logs/events.jsonl (best-effort)
```

Changed:

```
nak/harness/state.py            ToolCallRecord + record_tool() gained
                                `classification` (default "" — old state.json still loads)
nak/harness/executor.py         ToolExecutor classifies every result via observer
                                (ok = SUCCESS/PARTIAL_SUCCESS) and logs events;
                                AgentExecutor tags mirrored agent steps
                                (denied→NEEDS_USER, !ok→FAILURE, else SUCCESS)
nak/harness/runtime.py          observe() classifies final answers via observer +
                                events log
nak/harness/__init__.py         exports observer verdicts, log_event
```

Also fixed (found by testing): agent-internal tool calls bypassed workspace
isolation (a test `write_file` landed at repo root). Fix:

```
nak/plugins/tool/workspace.py   ambient sandbox: set_current_workspace() context
                                manager + current_workspace_root() (contextvars,
                                async-safe; None = legacy repo-root behaviour)
nak/plugins/tool/file_tool.py   _resolve() falls back to ambient root when
                                root=None (fs_tools inherits via shared _resolve)
nak/plugins/__init__.py         execute_plugin() simplified: explicit _root/_cwd
                                win, else ambient, else legacy; exec cwd defaults
                                to ambient too
nak/harness/executor.py         AgentExecutor.run_text() scopes the whole agent
                                run to state.workspace via set_current_workspace()
.gitignore                      workspace/ added (session data never committed)
```

Verified: 9/9 observer classifications green; events.jsonl written;
`test_decision_agent` 15/15 still green.

---

## 6. Phase 4 — Verification ✅ DONE

**Goal:** the harness — not the LLM's `{"answer": …}` — decides whether the task is done.

Built:

```
nak/harness/verifier.py         Verifier(workspace, timeout, checks).verify() ->
                                {ok, failures, checks:[{name, command, ok,
                                returncode, output_tail, elapsed}], log}
                                defaults = [compileall, pytest] run with
                                cwd=workspace; pytest exit 5 (no tests) = OK;
                                full log -> workspace/logs/verify_<ts>.log;
                                env: NAK_VERIFY_COMMANDS (JSON list or
                                ';'-separated), NAK_VERIFY_TIMEOUT (1-300s);
                                never raises (misconfig = failed check entry)
```

Changed:

```
nak/harness/runtime.py          should_verify(): True when the turn did real work
                                (tool_calls non-empty) in an isolated workspace;
                                pure-answer / ASK_USER flows untouched.
                                verify(): runs Verifier, appends
                                {ok, failures, log} to
                                state.verification_results[], logs event.
                                handle(): verify after agent run; failed
                                verification -> STATUS_FAILED + "[verification
                                FAILED: ...]" note appended to answer
                                (Phase 5 will auto-recover instead).
                                _result() gains top-level "verification" key
                                (compat: old keys unchanged).
```

Key principle (`.md` §11): **never trust "Done." — verify it.** Now enforced:
a `write_file` of syntactically broken Python passes the agent loop but fails
`compileall` → harness reports `failed`, not `done`.

Verified: verifier pass/fail cases green; runtime success path
(`done` + 1 passing verification) and failure path (`failed` + note) green
offline (FakeBrain); regression `test_harness_phases` +
`test_decision_agent` all green; agent writes confirmed inside workspace
(isolation assertion in test).

---

## 7. Phase 5 — Recovery ✅ DONE

**Goal:** `ERROR → Observer → Planner → Fix → Executor → Verifier` auto-retry with budgets.

Built:

```
nak/harness/recovery.py         max_retries() (env NAK_MAX_RETRIES, default 2,
                                clamped 0..5); decide_strategy(state) ->
                                RETRY (timeout/transient) | REPLAN (verify
                                failures + log tail) | ESCALATE (policy denials,
                                unknown agents, budget spent); build_fix_prompt()
                                (original request + failures + errors + verifier
                                log tail); recover() — one bounded fix attempt
                                via the ORIGINAL agent (Kepler fallback),
                                all steps recorded + events logged, never raises
```

Changed:

```
nak/harness/state.py            recovery_attempts counter (default 0 — old
                                state.json still loads)
nak/harness/runtime.py          should_recover(): last verification failed AND
                                strategy != ESCALATE. recover() wrapper.
                                handle(): verify -> while failed AND recoverable:
                                fix -> re-observe -> re-verify. Exhausted ->
                                STATUS_FAILED + note (+ attempts tried).
                                Success after fix -> done + "recovered": true.
```

No dedicated RecoveryAgent: the running agent replans with failure context
(Kepler is the fix-planner fallback). Verified offline: broken-write →
verify FAIL → replan overwrites fixed file → verify OK → `done/recovered`
(1 attempt, 2 verifications, `recovery` events); always-broken →
`failed` after exactly 1 attempt with `NAK_MAX_RETRIES=1` (no infinite loop).

---

## 8. Phase 6 — Multi-agent orchestration ✅ DONE

**Goal:** harness coordinates specialists.

```
Polaris (registry-driven — new agents picked up automatically)
    ├── Kepler (plans, roadmaps, designs)
    ├── ResearchAgent (research any topic)
    ├── Apollo      (new ✅ implements/fixes, verifies with tests)
    ├── Pulsar     (new ✅ pytest/compileall, regression tests)
    └── Astra      (new ✅ read-only review, ranked file:line findings)
```

Built:

```
nak/plugins/tool/agent_tools.py delegate(agent_name, task) (nested run_sync,
                                depth cap NAK_MAX_DELEGATION default 2);
                                create_plan(steps) / update_plan(append|complete|
                                clear) on the ambient turn state; loop-safe errors
nak/agents/_react.py            shared Mind-only ReAct loop (canonical loop for
                                ALL new agents: specialists + Genesis-
                                generated; legacy Planning/Research keep theirs)
nak/agents/Apollo|Pulsar|Astra/  thin specialists over _react
                                (run/run_sync/chat/chat_async compat)
```

Changed:

```
nak/agents/registry.json        + 3 entries (capabilities: coding/testing/review)
nak/plugins/tool/__init__.py    + AGENT_TOOLS / PLAN_TOOLS exports
nak/plugins/__init__.py         PLUGIN_TOOLS 13 -> 16; execute_plugin routes
                                delegate (needs brain) + plan tools (ambient state)
nak/plugins/tool/workspace.py   ambient harness-state slot (set_current/
                                current_harness_state, opaque object — no
                                plugins->harness import cycle); runtime sets it
                                in handle(), clears it in _result()
pipeline/chatagent/__main__.py  plan-first prompt lists current tool set
.gitignore                      (already) workspace/ never committed
```

Deferred as designed: async Brain stays sync (`requests` + single-flight —
sufficient for sequential orchestration; `httpx` only if real concurrency is
needed, see R9); plans persist per session since R4 (see below);
`delegate` outside a turn inherits no workspace — standalone callers scope
with `set_current_workspace()` (test does; in-turn calls are auto-scoped).

Verified offline (now 28 tests in `test_recovery_orchestration`, incl. R4/R6/R7
additions) + live (routing, full turns, continuity, recovery):
strategy matrix (budget/denied/timeout/failure), retry-budget parsing, new
agents resolvable + registered, decision prompt renders new names, delegate
offline + unknown-agent error + depth guard, plan tools inert outside a turn
and correct inside one, full orchestrated turn (Apollo plans → writes
valid code → verify OK → `done` with plan kept).

---

## 9. Tool capability roadmap (`.md` §8)

| Group | Have ✅ | Later ⬜ |
|---|---|---|
| Filesystem | read_file, write_file, edit_file, list_files, search_files | — |
| Execution | shell, python_exec, run_test, run_build | — |
| Memory | recall, remember, decide, build_context, agent_chat | — |
| Agent | agent_chat, delegate | — |
| Verification | compileall, pytest (via verifier) | lint, typecheck as checks |
| Planning | create_plan, update_plan (persisted per session) | — |

Harness-facing access stays `ToolRegistry.execute(...)`; agents never touch
`subprocess`/paths directly.

---

## 10. Risks / decisions carried forward

1. **Budgets first** (Phase 4/5): `max_retries` (env `NAK_MAX_RETRIES`, 2),
   verifier timeouts, `max_turns`, delegation cap (`NAK_MAX_DELEGATION`, 2) —
   all enforced and offline-tested. Infinite recover/delegate loops are
   structurally impossible (counters + caps).
2. **Async Brain**: deferred by design — sequential orchestration needs no
   concurrency; go `httpx` only if parallel sub-agents are ever required.
3. **`Genesis` template brace bug**: template renders through
   `str.format()` — every literal `{}` (incl. comments) must stay doubled;
   verify with `string.Formatter().parse()` when touching it. Contained by
   design now: the template is a thin shell over `nak.agents._react`
   (resolved by proper name from `nak/agents/shared.json`), so there is
   almost nothing brace-bearing left to break. (Legacy Planning/Research
   agents keep their own loop copies — migrate only with tests green.)
4. **Config**: all harness knobs are env + `nebulonak.cfg [harness]` file
   (`workspace_dir`, `verify_commands`, `verify_timeout`, `max_retries`,
   `max_delegation`) — env wins, file wins over built-ins, live-reloaded
   (see R10). Terminal-only knobs (`NAK_CHAT_PLANNING` / `--planning`) live
   in `pipeline/chatagent` by design (see R19).
5. **`nak_plan.md` repo-root writes**: Kepler auto-save now lands in the
   ambient workspace inside harness turns; standalone runs keep legacy
   repo-root behaviour. Full migration to `artifacts/` is optional polish.

---

## 11. Live testing ✅ DONE + remaining features

Live run (`nak/test/test_live_harness.py`,
`NAK_POLICY_TOOL_USE=allow_always NAK_TIMEOUT=150`,
Mind `glm-4.5-flash`, backend up):

- Brain basics: health/live OK, LLM configured, user `nmd_user_01` ensured,
  remember/search (5 hits)/chat (`LIVE-OK`) all OK.
- Live routing: "Write a Python module..." → `USE_AGENT Apollo` @ 0.95–1.0.
- Live turn: Apollo `list_files → write_file calc_live.py → python_exec`
  (self-verified `add(2,3)=5`), harness verification OK → `done`, file
  isolated in `workspace/session_*/project/` (repo root clean).
- Live Pulsar turn: wrote `test_sanity_live.py`, ran `run_test` → `done`.
  Finding: this environment had **no pytest installed** at the time —
  `run_test` honestly reported `No module named pytest` (→ fixed by R1).
- Live recovery path: initially unexercised (both turns passed first try);
  later proven live by R2 (dictated broken module → FAIL → replan → fix → OK).

### Remaining features (ordered)

- R1. **pytest as a real dependency.** ✅ DONE — moved to runtime deps
  (`pyproject.toml` + `uv.lock` metadata; installed 9.1.1 in `.venv`),
  `run_test` + verifier emit a clear install hint when pytest is missing
  (offline-tested), README documents it. Proven live: Pulsar
  `run_test` reports "1 passed, 0 failed" for real.
- R2. **Live recovery trial.** ✅ DONE — dictated broken module →
  verify FAIL (compileall) → 1 replan attempt → fixed → verify OK →
  `done`/`recovered` live against Mind.
- R3. **Cross-turn workspace continuity.** ✅ DONE — same-session turns share
  one workspace; follow-ups get injected context (files present + prior turn
  summaries, capped); fresh sessions start clean; terminal prints
  `[workspace: …]`; result carries top-level `workspace`. Proven live:
  turn 2 edited turn 1's module in place (single `mymath.py`, `add`+`mul`).
- R4. **Cross-turn plan persistence.** ✅ DONE — session plans carried into
  each new turn's state, included in follow-up context, saved back at turn
  end (explicit clear respected; never leaks across sessions; offline-tested).
- R5. **Lint/typecheck verifier checks.** OPEN — append ruff/flake8/mypy once
  the team picks tooling (`verify_commands` in cfg already supports it).
- R6. **Terminal transparency.** ✅ DONE — chat prints `[verify: OK/FAILED]`,
  `[recovery: N attempt(s), …]`, and the active `[plan: …]` block.
- R7. **`run_build` tool.** ✅ DONE — explicit command or auto-detect
  (Makefile → `make`, npm `build` script, `python -m build`, `setup.py`);
  clean error when nothing detected; missing `build` package gets an install
  hint; workspace-scoped; offline-tested. (17 tools total.)
- R8. **Dedicated RecoveryAgent.** OPEN — only if same-agent recovery proves
  weak in live use.
- R9. **Async Brain (httpx).** OPEN — only if parallel sub-agents are needed.
- R10. **`[harness]` cfg section.** ✅ DONE — `nebulonak.cfg [harness]` with
  `workspace_dir / verify_commands / verify_timeout / max_retries /
  max_delegation`, fully commented; env > cfg > defaults via
  `nak/utils/config.py` accessors; all four consumers rewired; offline-tested
  (cfg values, env-wins, invalid-fallback, repo parse).
- R11. **MCP integration.** OPEN — still a placeholder package.
- R12. **Housekeeping + incident hardening.**
  - `.md` / `nak_plan.md` / `tokyo.md`: user confirmed THEY deleted these
    files intentionally (earlier "unidentified process" note was a false
    alarm — cause found: the user). The 992-line `.md` proposal text is gone
    with it; its substance survives here in `new_plan.md`, which was derived
    from it. Lesson kept anyway: traces now record full tool args (below).
  - `nak_plan.md` auto-save could still move fully under
    `workspace/*/artifacts/` (open).
- R17. **Traceability + shell guardrails (done, incident-driven).**
  - `_react` steps now carry a truncated `args` preview; `AgentExecutor`
    mirrors it into `HarnessState.tool_calls` — every workspace trace shows
    WHAT ran, not just THAT something ran. (Legacy Planning/Research loops
    intentionally untouched.)
  - `shell()` refuses catastrophic shapes pre-execution: `rm -rf /|~|$HOME|/*`,
    `--no-preserve-root`, `rm` escaping its workspace cwd (incl. `/tmp/../`
    traversal), fork bombs, `mkfs`, `dd … of=/dev/*`. Ordinary cleanup
    (`rm -rf build`, `make clean`) still runs. Refusals return errors, so the
    observer classifies them honestly. Offline-tested (refuse + legit lists).
- R13. **Shared loop registry (done).** `nak/agents/_react.py` is declared by
  proper name in `nak/agents/shared.json`; the Genesis template resolves
  it from the manifest, so every newly generated agent imports the canonical
  loop instead of embedding a copy. Legacy Planning/Research keep theirs.
- R14. **Specialist routing rules (superseded by R16).** First attempt put
  explicit per-agent rules + a DISAMBIGUATION block into `polaris.md`. That
  worked but required a prompt edit per agent — replaced by data-driven
  triggers (R16); `polaris.md` is generic-only again and frozen by test.
- R15. **Project memory (done).**
  Turn-end one-liners to Mind (`[project <session>] request | files | agent(status): outcome`,
  `category=harness_project`, best-effort) + bounded recall (top-3, session-filtered,
  1200 chars) into follow-up workspace context; `[harness] project_memory`
  kill-switch (env `NAK_PROJECT_MEMORY` wins). Offline-tested 8/8; never fails a turn.
- R16. **Staged data-driven routing (REVERTED by user call).** An exact-trigger
  direct route (0 LLM calls) + top-K candidate prompts were built, tested
  (31 green) and proven live — then removed on review: code-based matching
  risks wrong picks on paraphrase/negation, while the LLM with the FULL
  list + descriptions + capabilities decides with complete knowledge. Lesson
  kept: `AgentMeta` briefly carried `triggers`; the durable replacement is
  disambiguating registry DESCRIPTIONS (Pulsar "does NOT implement",
  Astra "never modifies", Apollo "ANY write"), which the LLM reads
  every turn. `polaris.md` stays frozen generic; no bypasses, no filters.
  Token cost is O(N) per decision — trivial at current scale; revisit only
  with measured pain at 20+ agents.
- R18. **Description-fed routing accuracy (done).** Verified by probe that the
  full name + description + capabilities block reaches the prompt untruncated
  (~1.5k chars for 8 agents). `available_agents_str()` renders one agent per
  line (was `;`-separated) so scopes stay visually distinct. `polaris.md`
  rewritten around an explicit judge-from-NAME/DESCRIPTION/capabilities method
  (exclusions outrank overlap, most-specific-fit wins, never invent names) —
  still zero per-agent rules, freeze-guard green. Proven live against the new
  wording: "run the tests" → Pulsar, pandas/dashboard script →
  Apollo.
- R19. **Conditional pre-execution planning, terminal-layer only (done).**
  `pipeline/chatagent/planning.py` (new): modes `auto` (destructive/complex/
  multi-step/long signals, no LLM to decide) / `always` / `never`, resolved
  explicit-arg > `NAK_CHAT_PLANNING` env > `auto`. Planned turns pre-create
  the turn state, run `Kepler` scoped to its workspace, and seed
  `state.plan` — the runtime then feeds it to the worker as context with zero
  changes to `nak/harness` (guarded by test). CLI `--planning`, live
  `/planning` switch, `pipe.last_plan` for observability. Deliberately NOT
  mandatory: trivial turns stay single-call. R15 project memory untouched.

### Live findings that changed the code (2026-09-12)

- Mind + DB both died mid-session (stale pid files, connection refused);
  after user restart, testing resumed. Lesson: health-probe before live runs.
- Provider **429s** under burst use → README troubleshooting row; space out
  live runs (no code change — provider-side).
- Provider **400 "Prompt exceeds max length" (code 1261)** on turn 2+:
  server-side Mind session history grows unboundedly when one session is
  reused across turns. Fix: runtime mints a **fresh Mind session per turn**
  (`turn_session`, shared by the agent run + recovery attempts); cross-turn
  memory travels explicitly via workspace context. Recovery accepts an
  optional `session_id` override for the same reason.
- Agent emits tool JSON with **trailing commentary** → old
  first-`{`-to-last-`}` slice failed to parse and the call was silently
  dropped (turn "done" with nothing accomplished). Fix: string-aware
  **balanced-brace extractor** for the first complete JSON object in
  `nak/agents/_react.py` (+ offline tests). Generated agents keep their old
  parser (template risk untouched).
- Agent guessed `edit_file` args as `old`/`new` instead of
  `old_text`/`new_text` → fix: **alias tolerance** in `execute_fs_tool`
  (`old`/`new`/`file`/`filename`; canonical wins; + offline tests).
- Residual: a turn can still end `(no answer after tool loop)` when Mind
  spends all turns on tools without summarizing (known README case); file +
  verification were correct, only the summary was missing.

Quick regression at any point:
`python -m nak.test.test_decision_agent` (18 tests) +
`python -m nak.test.test_harness_config` (5 tests) +
`python -m nak.test.test_harness_phases` (observer/events/verifier/runtime) +
`python -m nak.test.test_recovery_orchestration` (28 tests) +
`python -m nak.test.test_chatagent_planning` (8 tests),
all offline. Live: `python -m nak.test.test_live_harness` (needs Mind + LLM).

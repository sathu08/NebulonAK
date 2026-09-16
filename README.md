# NebulonAK

**NebulonAK** — _Nebulon Agent Kit_ — is the agent/tool layer that **runs on NebulonMind**.
Everything runs on Mind only: there is no external LLM provider anywhere in the
codebase (no API keys for agents; see [Policy](#6-policy--approvals)).

```
NebulonDB (6969)  <-- REST -->  NebulonMind (9696)  <-- REST -->  NebulonAK / nak
   (truth/vectors/graph)        (memory + ranking)              (agents + tools + Mind loop)
```

`nak.brain` is the thin **brain adapter** — it does not re-implement storage.
Everything it does is an HTTP call to `NebulonMD` at `nebulonak.cfg: base_url`.

---

## Layout

```
NebulonAK/
  nebulonak.cfg          # <-- base_url + user + policy (you edit this)
  nak/
    brain/               # the brain adapter (only connection to NebulonMind)
      client.py          # Brain class: health, create_user, remember, search, chat, ...
      __main__.py        # CLI: python -m nak.brain --check
    utils/
      config.py          # loads nebulonak.cfg + env overrides
      agent_registry.py  # JSON leader registry for nak/agents/*
    plugins/             # generic tools agents can call
      __init__.py        # PLUGIN_TOOLS + execute_plugin() single dispatch
      tool/
        memory_tools.py  # recall/remember/decide/build_context/agent_chat (via Brain)
        file_tool.py     # read_file/write_file (txt/json/csv/pdf/excel/docx)
        policy.py        # approval gates for tool use + agent creation
      mcp/               # MCP integration (planned, placeholder only)
    tools/               # backward-compat shims -> nak.plugins.tool (do not extend)
    prompts/             # ALL prompt text lives here (no prompts in code)
      polaris.md        # Polaris routing prompt
      capabilities.md    # capability-set proposal prompt
      agent_naming.md    # reusable-name proposal prompt
    agents/
      Polaris/     # router: USE_AGENT / CREATE_AGENT / ASK_USER (via Mind)
      Genesis/      # confirm-gated scaffolding for new agents
      Kepler/     # reusable: plans/roadmaps/designs for any task
      ResearchAgent/     # reusable: research any topic
      registry.json      # source of truth: which agents exist
    test/
      test_brain_connection.py  # connectivity probe
      test_decision_agent.py    # 15 unit tests (decision + registry + creator)
  pipeline/
    chatagent/           # terminal chat pipeline (decision-first validation loop)
      pipeline.py        # ChatAgentPipeline: decide -> route -> run-or-ask
      __main__.py        # CLI: python -m pipeline.chatagent
```

---

## 1. Config -- `nebulonak.cfg`

```ini
[nebulonmind]
base_url = http://localhost:9696/api/NebulonMind
user = nmd_user_01
timeout = 30
connect_timeout = 5
read_timeout = 30
write_timeout = 60
verify_ssl = false
auth_token =

[brain]
default_top_k = 5
default_max_characters = 6000
auto_create_user = true
persist_user = false

[policy]
tool_use = ask            # ask | allow_once | allow_always | no
create_agent = ask        # ask | allow_once | allow_always | no
max_turns = 6
```

Overrides (env wins over cfg):

- `NAK_BASE_URL` / `NEBULONMIND_BASE_URL`
- `NAK_USER` / `NAK_USERNAME`
- `NAK_AUTH_TOKEN` / `NMD_API_AUTH_TOKEN`
- `NAK_TIMEOUT` (raises every request budget, incl. `Brain.chat`)
- `NAK_POLICY_TOOL_USE` / `NAK_POLICY_CREATE_AGENT` / `NAK_POLICY_MAX_TURNS`
- `NAK_HOME` / `NEBULONAK_HOME` -> directory containing `nebulonak.cfg`

Invalid policy values normalize to `ask`. See [Policy](#6-policy--approvals).

---

## 2. Brain -- `nak.brain`

```python
from nak.brain import Brain

brain = Brain()  # reads nebulonak.cfg
print(brain.health())
brain.ensure_user()  # idempotent: create if missing

# store & recall
brain.remember("I am building NebulonAK, an agent kit on NebulonMind")
print(brain.search("what am I building?"))

# bounded context for an LLM
ctx = brain.context("what am I building?", top_k=5)
print(ctx["context"])

# in-process agent (uses NebulonMind's LLM provider)
reply = brain.chat("What do you remember about my project?")
print(reply["answer"])
```

### CLI

```bash
# full probe: health + LLM + user + write/search
python -m nak.brain --check

# health only
python -m nak.brain --health

# user
python -m nak.brain --create-user myuser
python -m nak.brain --resolve-user myuser

# memory
python -m nak.brain --remember "I love Rust"
python -m nak.brain --search "Rust"
python -m nak.brain --context "Rust"

# agent chat (needs NebulonMind LLM configured)
python -m nak.brain --chat "Summarize my stored memories"
```

### Connectivity test script

```bash
python -m nak.test.test_brain_connection
```

It prints a step-by-step report and exits 0 on `ok: true`.

---

## 3. Polaris -- the router

Every pipeline turn goes through `Polaris.decide()` **first** (stateless, so the
decision prompt never pollutes chat history). It returns a `DecisionResult`:

| action | meaning |
|---|---|
| `USE_AGENT` | an installed agent can handle it (`agent_name` set) |
| `CREATE_AGENT` | none can; proposes a reusable `agent_name` (never task-specific: `Kepler`, not `FastAPIKepler`) |
| `ASK_USER` | too vague, or Mind unreachable; `reason` is the clarifying question |

- Invalid JSON / unreachable Mind never raises to the caller (unless `strict=True`):
  it degrades to `ASK_USER` with `confidence=0.0`.
- Agent discovery is JSON-led: `nak/agents/registry.json` is the leader,
  mirrored to Mind memory for semantic fallback (`AgentRegistry`).
- `Genesis.create()` is **confirm-gated**: it cannot write without
  `await confirm(spec) == True`. Existing names/folders raise `FileExistsError`.
- Shipped specialists (in `registry.json`, routed by name — never recreated):
  `Apollo` (implement/fix, verifies its own work), `Pulsar`
  (runs suites, reports pass/fail), `Astra` (read-only audit, ranked
  findings). Routing is **always the LLM's own decision**: every `decide()`
  sees the FULL agent list with descriptions + capabilities, and judges with
  complete knowledge — no code pre-filters, no routing bypasses. Overlap
  disambiguation lives in the registry descriptions themselves (e.g.
  Pulsar's states it never implements; Astra's states it never
  modifies), never in `polaris.md` prose — that file holds generic
  principles only and is frozen (a test fails if per-agent rules return).
  `CREATE_AGENT` is reserved for genuinely new domains (e.g. `ExcelAgent`),
  never for these three.

---

## 4. chatagent pipeline -- terminal chat

```bash
python -m pipeline.chatagent
python -m pipeline.chatagent --user alice
python -m pipeline.chatagent --once "create a trip plan to japan"
python -m pipeline.chatagent --planning always   # auto (default) | always | never
```

Every reply prints what decided and what ran:

```
[decision] action=USE_AGENT agent=Kepler conf=0.95
[Running on: Kepler]
[tools used: recall]
assistant> ...
```

`[tools used: …]` lists each tool the agent's loop executed (`(failed)` marks
tool errors read from the result payload, `(denied)` marks policy/user refusals).
No silent fallbacks: a missing agent becomes a creation proposal, never a quiet
`Example` run. Note: `ok` is read from the tool result (`"error" not in …`),
not from the absence of an exception — executors answer errors as JSON.

### Slash commands

| command | effect |
|---|---|
| `/agents` | list registered agents (registry leader) |
| `/create-agent <desc>` | guided creation: asks name → capabilities `1/2/3` → custom instructions → confirm |
| `/health` | Mind liveness + LLM status |
| `/remember <text>` / `/search <query>` | direct Brain utilities |
| `/session` / `/new-session` | show / rotate the Mind chat session |
| `/decision on\|off` | toggle decision JSON per turn |
| `/planning auto\|always\|never` | pre-execution planning mode for this session |
| `/exit`, `/quit`, `/bye` | leave |

### Pre-execution planning (`auto` by default)

Before complex turns, the terminal runs a `Kepler` draft and seeds it
into the turn's plan (visible in the `[plan:]` block, fed to the worker as
context). Modes: `auto` (destructive/complex/multi-step/long requests only —
no LLM call to decide this), `always`, `never`. Set per-run with
`--planning` or env `NAK_CHAT_PLANNING`, switch live with `/planning`.
Lives entirely in `pipeline/chatagent/` (`planning.py`) — `nak/harness` is
untouched, and `never` keeps turns byte-identical to unplanned ones.
Project memory (auto-remembering project facts to Mind) is **not** built —
tracked as a separate future item.

### Guided `/create-agent` flow

```
you> /create-agent agent that summarizes my emails
Agent name? [EmailAgent]:                       # Enter = accept model suggestion, or type your own
Capabilities for 'EmailAgent' (pick 1, 2 or 3):
  1. general (Recommended)                      # model-proposed when Mind is up, pre-structured fallback otherwise
  2. chat, remember, recall
  3. custom (type your own)
Choose [1]: 1
Custom instructions for this agent? [Enter to skip]: Always answer in French
Create 'EmailAgent' — ... — caps: general — instructions: Always answer in French
Create? [y/N or 1/2]: y
created: nak/agents/EmailAgent
```

Names are validated (`validate_agent_name`) and model proposals prefer reusable
names (`agent_naming.md`). All yes-decisions accept `y / yes / 1`.

### Turn anatomy (create path)

```
you> i want to do research on databricks
[decision] CREATE_AGENT ResearchAgent conf=0.95
[Running on: Polaris]
No suitable agent found ... Proposed: create 'ResearchAgent' — ...
Can I create agent 'ResearchAgent'? [y/N or 1/2]: y     # NO -> guidance only, nothing created/executed
created: nak/agents/ResearchAgent
[Running on: ResearchAgent] (first run: plan only)
assistant (plan)> PLAN: 1. gather sources 2. summarize ...   # plan lists the tools it will use
Approve to begin? [y/N or 1/2]: y                           # NO -> stops, plan stays visible
[Running on: ResearchAgent]
[tools used: recall, write_file]
assistant> RESULT: ...
```

---

## 5. Plugins -- generic tools (`nak/plugins`)

One list + one call for any LLM loop or agent:

```python
from nak.brain import Brain
from nak.plugins import PLUGIN_TOOLS, execute_plugin

result_json = execute_plugin(Brain(), "read_file", {"path": "notes/todo.md"})
```

| tool | needs Brain | what it does |
|---|---|---|
| `recall` / `remember` / `decide` | yes | memory proxy (`memory_tools.py` → `NEBULONAK_TOOLS`) |
| `build_context` / `agent_chat` | yes | bounded context / full Mind turn |
| `read_file` | no | text+code (30+ exts), parsed `.json`, parsed `.csv/.tsv`, `.pdf`/`.xlsx`/`.docx` via optional deps (`pypdf`, `openpyxl`, `python-docx`) with a clear install message when absent |
| `write_file` | no | `create` (fail if exists) / `overwrite` / `append`, sandboxed under repo root (`..` escapes rejected), parents auto-created |

Safety: binaries (null bytes) refused, size/row caps with `truncated` flags,
executors always return JSON strings (errors as `{"error": …}`, loop-safe).
`nak/plugins/mcp/` is a placeholder package holding the MCP server/client plan.
All tools live under `nak.plugins.*` (the old `nak.tools.*` shims were removed).

---

## 6. Policy + approvals

`[policy]` (or `NAK_POLICY_*` env) governs autonomy. Prompts accept `y/1`;
non-interactive input (EOF) denies unless `allow_always`.

| value | `tool_use` | `create_agent` |
|---|---|---|
| `ask` (default) | prompt on every tool call | confirm every creation |
| `allow_once` | approve once per agent run (covers that task after you see the plan) | confirm once per session, then auto-create |
| `allow_always` | never ask | auto-create without asking |
| `no` | tools disabled (Mind reasoning only; calls skipped with notice) | proposals only, never created |

Enforcement lives in `nak/plugins/tool/policy.py` (`approve_tool_use`,
creation consult in the terminal), read live from cfg every time — no restarts
needed to change policy.

---

## 7. Agents -- Mind-only ReAct loop

Agents run a shared tool loop **on NebulonMind** — no external keys, ever:

1. build context: `role_hint + custom_instructions` prefixed to the request
   (Mind accepts only `user/assistant/tool` roles — `system` is rejected, so
   context travels as text, never as a message);
2. ask Mind for the next step as strict JSON: `{"tool":…, "arguments":…}` or
   `{"answer":…}` (unparseable replies become the final answer — never a crash);
3. policy-gate each tool call, execute via `execute_plugin`, feed results back;
4. stop at an answer or `max_turns`; return `{answer, turns, steps, agent}`.

The loop lives **once**, in `nak/agents/_react.py` (`run_react_loop` /
`parse_step_answer`), declared in `nak/agents/shared.json` under its proper
name `_react`. `Apollo` / `Pulsar` / `Astra` and every
`Genesis`-generated agent run it — never an embedded copy.
(`Kepler`/`ResearchAgent` predate it and keep their own aligned copies.)

The three specialists **ship with the repo** (`created_via: NAK` in
`registry.json`) — they are the harness's standard library, not something to
create live. `Genesis` exists for genuinely new domains (file formats,
internal tools). See §3 for how the router tells them apart.

`custom_instructions` (from `/create-agent`) are baked into `agent.py` at
creation time (quote/brace/newline-escaped so generated code stays valid).
Every completed `Kepler` run also saves its output to `nak_plan.md`
(repo root) for user reference — error markers are never saved, and a failed
save never fails the run.

---

## 8. Prompts -- single source of truth

**No prompt text in code.** All prompts live in `nak/prompts/*.md` and load via
`from nak.prompts import render_prompt, load_prompt`:

- `polaris.md` — routing rules incl. generic-naming (`Kepler`, not `FastAPIKepler`)
- `capabilities.md` — capability-set proposals (`{description}`)
- `agent_naming.md` — reusable-name proposals (`{description}`)

A missing prompt file fails loud (`FileNotFoundError`) instead of silently
deciding from a stale inline copy. The `Genesis` agent template must
double every literal brace (`{{…}}`) because it renders through `str.format()`
— **including inside comments** (format scans the whole template; a `{"…": …}`
in a comment broke creation once). The template itself stays tiny because the
loop lives in `nak.agents._react` (resolved via `nak/agents/shared.json`, so a
rename touches the manifest, not the template). Verify with
`string.Formatter().parse()`
(only `name`/`reason`/`reason_lower`/`react_module` may appear).

---

## 9. Validation -- offline-proven + live

**Offline-proven** means: verified with fakes, no Mind/network required, in
milliseconds. The pattern used for every feature in this repo:

- `FakeBrain` — scripted `chat()` (decision JSON by prompt shape, plan/final
  answers by prefix), recording `remember`/`search`; unexpected calls raise.
- Stubbed `input()` (answer queues) to drive confirm/approve/choice prompts,
  including EOF paths.
- Isolated disk (`tempfile` registry + agents root) for real `Genesis`
  runs; generated files are imported and executed.
- Scripted Mind replies (`{"tool":…}` → `{"answer":…}`) to drive the agent loop,
  including error-recovery and policy-`no` paths.

```bash
python -m nak.test.test_decision_agent   # 18 tests: parse, decide, fallback, registry, creator gates + shared-loop manifest/template/generated-agent
```

Covered offline: decide→route→run for all three actions, NO/YES/approve-denied
creation flows, guided `/create-agent`, policy modes, tool loop incl. template
brace-correctness, file tools (text/json/csv/binary/missing/sandbox-escape,
create/overwrite/append). **Live Mind runs** then confirm routing end-to-end
(`USE_AGENT Kepler 0.95` for a Japan trip, `CREATE_AGENT ResearchAgent`
→ created → plan → approve → execute).

---

## Requirements

- Running `NebulonDB` at `localhost:6969` (via `nebulondb start`)
- Running `NebulonMind` at `localhost:9696` (via `nebulonmind start`)
- Python 3.10+ (`pip install -e .`; full deps in the bundled `.venv`)
- `pytest` installed in the same Python (`pip install pytest` — it ships as a
  runtime dependency because the harness verifier and `run_test` execute it;
  without it, test checks fail with an install hint instead of running)
- No LLM keys needed: agents reason and loop purely on NebulonMind

---

## Verify NebulonMind is up

```bash
curl http://localhost:9696/api/NebulonMind/health
curl http://localhost:9696/api/NebulonMind/health/live
curl http://localhost:9696/api/NebulonMind/llm/status
```

## Troubleshooting

| symptom | cause / fix |
|---|---|
| `ASK_USER … NebulonMind unreachable` | Mind/DB down — restart both, retry (pipeline degrades, never crashes) |
| `[brain error: … Read timed out]` | one Mind call exceeded 60s; Mind calls take 15–45s+ under load — retry, or raise with `NAK_TIMEOUT=150` (or `timeout`/`write_timeout` in cfg) |
| `(no answer after tool loop)` | Mind spent all `max_turns` calling tools without summarizing — raise `max_turns` |
| `request validation failed … role` | a `system`-role message was sent; Mind accepts only `user/assistant/tool` (fixed in all shipped agents) |
| stale agent proposed (folder missing) | `registry.json` drifted from disk — pipeline converts it into a create proposal |
| `Polaris parse failed …` in test output | expected: negative tests feeding garbage to prove `ASK_USER` fallback (suite still green) |
| `429 Too Many Requests` from Mind / provider | rate-limited after burst use — wait a minute and retry; space out live test runs |
| `Prompt exceeds max length` (code 1261) on later turns | stale server-side session growth — current runtime mints a fresh Mind session per turn, so update if you see this on old code |

# NebulonAK — Nebulon AgentKit

NebulonAK (NAK) is a modular agent framework. A user connects to
[NebulonMind](https://github.com/sathu08/NebulonMD) + NebulonDB, gives NAK a
task, and NAK plans, delegates, executes, verifies and reports it.

**Golden rule:** NAK performs no LLM inference itself. Every model call goes
through `nak_host.brain` → NebulonMD HTTP API. Provider, model and keys live in
NebulonMD's config — never in this repo.

Start order: **NebulonDB (`:6969`) first, then NebulonMD (`:9696`), then AK.**

## Layout

```text
nak_host/
  domain/        Task · TaskRun · Result · policy · ports · state · errors (no IO)
  application/   create/plan/assign/run/verify + agent loop + chains + approval ledger
  adapters/      Brain/Mind · Memory/DB · JSON registry · NebulonDB + memory stores
                 harness executor · Mind credentials
  harness/       sandboxed tool runner (file, directory, terminal, search,
                 observe, assertion, trace, scorecard, report) + crafts
  worker/        GenericWorker (identity, prompt, chat, team, dispatch)
  decision/      laya-backed task router + policy evaluator
  api/           laya decision endpoint + console dashboard (`:8000`)
                 routes/ (status, pipeline, settings, dashboard) + web/ console
  cli/           server ops: start|stop|restart|status (`python nebulonak.py …`)
  brain/         NebulonMD HTTP client (the only LLM path)
  manifests/     workers.json · teams.json · crafts.json · tools.json · permissions.json
  utils/         settings (nebulonak.cfg + env) · constant.py (single source)
  wiring.py      composition root — the only place that wires everything
nebulonak.py     server entry point (mirrors nebulondb.py / nebulonmd.py)
nebulonagentkit.py  foreground API runner (same app, no daemon)
pipeline/
  vetting/       worker evaluation with nunan as assessor (`nak-vetting`)
docs/
  ARCHITECTURE.md  layers, contracts, ports, TaskStore, state machine
unittest/        contract + integration + live + server tests
```

Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quickstart

```bash
uv pip install -e . --python .venv/bin/python   # or: pip install -e .

# 1. Mind reachable? (DB first, then Mind)
.venv/bin/python -c "from nak_host.brain import Brain; print(Brain().test_connection())"

# 2. Talk to a worker (one Brain call, offline commands need no Mind)
.venv/bin/python -m nak_host.worker --list
.venv/bin/python -m nak_host.worker --worker vizhi --prompt
.venv/bin/python -m nak_host.worker --worker arivu --chat "hello"

# 3. Run a craft: plan -> approve -> peer workers run in threads
.venv/bin/python -m nak_host.worker --worker vizhi --craft "write log_analyzer.py" --approve

# 3b. Serve the API + web console (status, pipeline, settings pages)
python nebulonak.py start
# open http://127.0.0.1:8000/api/NebulonAK/dashboard/
python nebulonak.py status   # stop | restart [--foreground]

# 4. Python API (durable task store)
.venv/bin/python - <<'EOF'
from nak_host.wiring import make_brain, make_harness, make_worker, make_prod_store, run_craft
store = make_prod_store()            # NebulonDB; NAK_STORE=memory keeps tests offline
w = make_worker("arivu", brain=make_brain())
h = make_harness(worker="arivu")
print(run_craft("ship login", worker=w, harness=h, approver=lambda info: True, store=store))
EOF

# 5. Vet workers (foreground API run without the daemon)
.venv/bin/nak-vetting run --workers arivu,ezhilan
.venv/bin/python nebulonagentkit.py
```

## Configuration

Everything lives in `nebulonak.cfg` (one file) plus env overrides — see
`nak_host/utils/settings.py`. All built-in fallback values live in
`nak_host/utils/constant.py` (single source; modules import from there).

| Area | File / env |
|---|---|
| Project home | `[paths] nebulonak_home`, empty = cfg folder; `NAK_HOME` wins (web/logs/pid/runs derive from it) |
| Server | `[server] host/port/workers/timeout/keep_alive/graceful_timeout/log_level`; `NAK_SERVE_HOST/PORT/...` win |
| Decision endpoint | `[decision] endpoint/timeout/auth_token`; `NAK_DECISION_*` win |
| Mind connection | `[nebulonmind]` in cfg, `NAK_BASE_URL / NAK_USER / NAK_AUTH_TOKEN / NAK_TIMEOUT` |
| Task store | `make_prod_store()` (NebulonDB, credentials resolved from Mind); `NAK_STORE=memory` for offline/tests |
| Workspace | `[harness] workspace_root`, empty = `<project>/workspace`; `NAK_WORKSPACE` wins |
| Workers/teams/crafts/tools/permissions | `nak_host/manifests/*.json` (durable contracts — edit the JSON, no code changes) |

No `[llm]` section exists anywhere here by design.

## Testing

```bash
.venv/bin/python -m pytest -q        # full suite (offline; 2 live tests skip without a DB)
.venv/bin/ruff check nak_host/ unittest/
```

Conventions: domain imports nothing outward; application depends only on
ports; adapters implement ports; `wiring.py` is the only module that imports
all layers; interfaces contain no business logic.

# NebulonAK

**NebulonAK** — _Nebulon Agent Kit_ — is the agent/tool layer that **runs on NebulonMind**.

```
NebulonDB (6969)  <-- REST -->  NebulonMind (9696)  <-- REST -->  NebulonAK / nak
   (truth/vectors/graph)        (memory + ranking)              (agents + tools + LLM loop)
```

`nak.brain` is the thin **brain adapter** — it does not re-implement storage.
Everything it does is an HTTP call to `NebulonMD` at `nebulonak.cfg: base_url`.

---

## Layout

```
NebulonAK/
  nebulonak.cfg          # <-- base_url + user (you edit this)
  nak/
    brain/               # the brain adapter (only connection to NebulonMind)
      client.py          # Brain class: health, create_user, remember, search, chat, ...
      __main__.py        # CLI: python -m nak.brain --check
    utils/config.py      # loads nebulonak.cfg + env overrides (NAK_BASE_URL, NAK_USER, ...)
    tools/tools.py       # OpenAI-compatible tool schemas (NEBULONAK_TOOLS)
    prompts/             # prompt templates for agents (base.md, planning.md)
    agents/
      Example/
        base.py          # SimpleAgent (Brain-only, no api_key)
    test/
      test_brain_connection.py  # connectivity probe
```

---

## 1. Config -- `nebulonak.cfg`

At the repo root:

```ini
[nebulonmind]
base_url = http://localhost:9696/api/NebulonMind
user = nmd_user_01
timeout = 30
auth_token =

[brain]
default_top_k = 5
auto_create_user = true
```

Overrides (env wins over cfg):

- `NAK_BASE_URL` / `NEBULONMIND_BASE_URL`
- `NAK_USER` / `NAK_USERNAME`
- `NAK_AUTH_TOKEN` / `NMD_API_AUTH_TOKEN`
- `NAK_HOME` -> directory containing `nebulonak.cfg`

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
# or
python nak/test/test_brain_connection.py
```

It prints a step-by-step report and exits 0 on `ok: true`.

---

## 3. Tools for LLM -- `nak/tools`

Expose memory as OpenAI tools (LLM-agnostic, works with any provider via NebulonMind):

```python
from nak.brain import Brain
from nak.tools import NEBULONAK_TOOLS, execute_tool
import openai

brain = Brain()
client = openai.OpenAI(
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key="...")

resp = client.chat.completions.create(
    model="glm-4.5-flash",
    messages=[{"role": "user", "content": "Remember I use Python"}],
    tools=NEBULONAK_TOOLS,
)
tc = resp.choices[0].message.tool_calls[0]
result = execute_tool(brain, tc.function.name, tc.function.arguments)
```

Tool definitions live in `nak/tools/tools.py` (`NEBULONAK_TOOLS`), prompts in `nak/prompts/` (`.md` / `.j2` / `.txt`, Jinja2 optional), all via `Brain`.

---

## Requirements

- Running `NebulonDB` at `localhost:6969` (via `nebulondb start`)
- Running `NebulonMind` at `localhost:9696` (via `nebulonmind start`)
- Python 3.10+, `requests`, `openai` (for agent loop)

---

## Verify NebulonMind is up

```bash
curl http://localhost:9696/api/NebulonMind/health
curl http://localhost:9696/api/NebulonMind/health/live
curl http://localhost:9696/api/NebulonMind/llm/status
```

# NebulonAK LIVE Report — real NebulonMD (http://localhost:9696)

Date: 2026-10-15 · Mind: NebulonMind v0.1, backend up, LLM openrouter/nvidia/nemotron-3.5-lightning:free (configured, has_key=True)
Suite: `unittest/live/test_live_mind.py` — **5 passed, 0 failed** (offline suite still 110/110)

Run: `uv run --with pytest python -m pytest unittest/live/test_live_mind.py -v -s`
Note: each worker chat = triage LLM call + main LLM call (15-90s each under load).

## Results
1. `test_live_health_and_llm` (1s) — live/ready/backend up, LLM configured. PASS
2. `test_live_vizhi_chat_simple` (184s) — vizhi answers in identity:
   "I'm vizhi, a calm and practical project manager who prioritizes quality, deadlines, and transparency."
   triage policy=chat. PASS
3. `test_live_arivu_plan` (69s) — arivu returns numbered README plan; worker enforces max 3 steps live:
   "[plan truncated to 3 steps]", extract_plan=3 steps. PASS
4. `test_live_memory_recall_store` (137s) — stored `live-marker-XXXX loves filter coffee`, recall query returns it:
   "The memory states that live-marker-e2c7a6 loves filter coffee." recalled=True. PASS
5. `test_live_tool_permission_craft` (56s) — approved harness file write+read ok, arivu same write denied (boundary holds live), craft plan expands, propose_plan live planned=True, needs_approval=True, approved=True, 2 steps written to nak_plan.md. Triage endpoint_live=True, policy chat. PASS

## Worker × tool / permission / craft — live verdict
- vizhi: chats, plans, tool write with approver works, plan file persists, triage+policy chat.
- arivu: plans only, truncation enforced on real LLM output, write denied.
- tools: file write/read live in session dir ok.
- permissions: vizhi ask→allow with approver, arivu deny — same as offline, confirmed live.
- crafts: plan expand + propose_plan end-to-end works against live Brain.

Side effects: probe memories under `ak_live_*` users + session dirs under `workspace/live-*` (safe to prune).

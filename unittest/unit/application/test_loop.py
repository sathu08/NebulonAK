"""Agent loop tests (scripted brains, fake executor, memory store)."""

from __future__ import annotations


from nak.adapters.store_memory import InMemoryTaskStore
from nak.application import ApprovalLedger, create_task
from nak.application.loop import (
    allowed_tool_schemas,
    parse_tool_calls,
    render_tool_prompt,
    run_loop,
)
from nak.domain import ExecutionContext, Verdict
from nak.domain.errors import TaskCancelled
from nak.domain.ports import ToolResult


class ScriptBrain:
    """Scripted answers; records raw flag + message roles."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def chat(self, text, messages=None, session_id=None, raw=False):
        self.calls.append({"text": text, "messages": list(messages or []),
                           "raw": raw})
        return {"answer": self.answers.pop(0) if self.answers else "done"}


class StrictBrain(ScriptBrain):
    """Old brain shape: no raw kwarg (exercises the TypeError fallback)."""

    def chat(self, text, messages=None, session_id=None):
        self.calls.append({"text": text, "messages": list(messages or [])})
        return {"answer": self.answers.pop(0) if self.answers else "done"}


class FakeExecutor:
    def __init__(self, verdicts=None, results=None):
        self.verdicts = verdicts or {}
        self.results = results or {}
        self.inspected = []
        self.runs = []

    def inspect(self, tool, args, ctx):
        self.inspected.append((tool, dict(args), ctx))
        return self.verdicts.get(tool, Verdict.ALLOW)

    def run(self, tool, args, ctx):
        self.runs.append((tool, dict(args), ctx))
        res = self.results.get(tool)
        if isinstance(res, Exception):
            raise res
        if res is not None:
            return res
        return ToolResult(ok=True, data={"echo": dict(args)})


class _Cancel:
    def __init__(self, cancelled=False):
        self._cancelled = cancelled

    @property
    def cancelled(self):
        return self._cancelled

    def raise_if_cancelled(self):
        if self._cancelled:
            raise TaskCancelled("cancelled")


def _task(store, **overrides):
    values = {"goal": "do it", "workspace": "/tmp/ws", "max_steps": 4,
              "timeout": 60}
    values.update(overrides)
    task, _ = create_task(values.pop("goal"), store=store, **values)
    return task


def _ctx(worker="arivu", approval=None, authority=None, cancelled=False):
    return ExecutionContext(
        task_id="t1", worker_id=worker, workspace="/tmp/ws",
        allowed_tools=frozenset({"file"}), attempt=1, approval=approval,
        policy=object(),
        authority=authority if authority is not None else ApprovalLedger(),
        cancel=_Cancel(cancelled))


def test_parse_and_render():
    calls, malformed = parse_tool_calls(
        'text\n```json\n{"tool": "file", "arguments": {"a": 1}}\n```\n'
        '```json\n{"nope": 1}\n```\n```json\nbroken\n```')
    assert calls == [{"tool": "file", "arguments": {"a": 1}}]
    assert malformed == 2
    prompt = render_tool_prompt([{"name": "file", "description": "files",
                                    "args": {"action": "read|write"}}])
    assert "- file: files" in prompt and "at most 3 per turn" in prompt
    assert '"action": "read|write"' in prompt
    assert "NEVER claim a tool succeeded" in prompt
    assert "copy this shape exactly" in prompt


def test_react_two_turns_and_raw_and_roles():
    store = InMemoryTaskStore()
    task = _task(store)
    brain = ScriptBrain(['```json\n{"tool": "file", "arguments": {"action": "read"}}\n```',
                         "all done"])
    out = run_loop(task, None, store=store, brain=brain,
                   executor=FakeExecutor(), ctx=_ctx())
    assert out["status"] == "done"
    assert out["answer"] == "all done"
    assert out["steps"] == 2
    assert all(c["raw"] for c in brain.calls)
    assert "EXECUTING now" in brain.calls[0]["messages"][0]["content"]
    roles = set()
    for call in brain.calls:
        roles.update(m.get("role") for m in call["messages"])
    assert roles <= {"tool", "user", "assistant"}
    assert len(out["tool_calls"]) == 1 and out["tool_calls"][0]["ok"]


def test_strict_brain_fallback():
    store = InMemoryTaskStore()
    out = run_loop(_task(store), None, store=store,
                   brain=StrictBrain(["done"]),
                   executor=FakeExecutor(), ctx=_ctx())
    assert out["status"] == "done"


def test_deny_ask_grant_and_stall():
    store = InMemoryTaskStore()
    task = _task(store)
    block = '```json\n{"tool": "terminal", "arguments": {}}\n```'
    # Denied every turn -> stalls out.
    out = run_loop(task, None, store=store, brain=ScriptBrain([block] * 6),
                   executor=FakeExecutor(
                       verdicts={"terminal": Verdict.DENY}), ctx=_ctx(),
                   max_stalls=2)
    assert out["status"] == "stalled"
    # ASK with no approver -> needs_approval stop (stalls beat max_steps).
    out = run_loop(task, None, store=store, brain=ScriptBrain([block] * 6),
                   executor=FakeExecutor(
                       verdicts={"terminal": Verdict.ASK}), ctx=_ctx(),
                   max_stalls=1)
    assert out["status"] == "needs_approval"
    # ASK + approver -> grant flows into the executed ctx.
    ledger = ApprovalLedger()
    ctx = _ctx(authority=ledger)
    executor = FakeExecutor(verdicts={"terminal": Verdict.ASK},
                            results={"terminal": ToolResult(ok=True, data={})})
    out = run_loop(task, None, store=store, brain=ScriptBrain([block, "ok"]),
                   executor=executor, ctx=ctx, ledger=ledger,
                   approver=lambda info: info["tool"] == "terminal",
                   max_stalls=5)
    assert out["status"] == "done"
    assert executor.runs and executor.runs[0][2].approval is not None
    assert ledger.is_valid(executor.runs[0][2].approval) is True


def test_max_steps_cancel_and_brain_failure():
    store = InMemoryTaskStore()
    block = '```json\n{"tool": "file", "arguments": {}}\n```'
    out = run_loop(_task(store, max_steps=2), None, store=store,
                   brain=ScriptBrain([block] * 5),
                   executor=FakeExecutor(), ctx=_ctx())
    assert out["status"] == "max_steps" and out["steps"] == 2
    out = run_loop(_task(store), None, store=store,
                   brain=ScriptBrain(["done"]), executor=FakeExecutor(),
                   ctx=_ctx(cancelled=True))
    assert out["status"] == "cancelled"

    class Broken:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    out = run_loop(_task(store), None, store=store, brain=Broken(),
                   executor=FakeExecutor(), ctx=_ctx())
    assert out["status"] == "failed"


def test_malformed_blocks_retry_then_finish():
    store = InMemoryTaskStore()
    out = run_loop(_task(store), None, store=store,
                   brain=ScriptBrain(["```json\nnope\n```", "fine"]),
                   executor=FakeExecutor(), ctx=_ctx())
    assert out["status"] == "done" and out["answer"] == "fine"


def test_deny_reason_reaches_model():
    class Explaining(FakeExecutor):
        def inspect_detail(self, tool, args, ctx):
            return Verdict.DENY, "missing required args: ['content']"

    store = InMemoryTaskStore()
    block = '```json\n{"tool": "file", "arguments": {"action": "write"}}\n```'
    brain = ScriptBrain([block, "done"])
    out = run_loop(_task(store), None, store=store, brain=brain,
                   executor=Explaining(), ctx=_ctx())
    assert out["status"] == "done"
    observations = [m["content"] for call in brain.calls for m in call["messages"]
                    if m.get("role") == "tool"
                    and "Observation" in m.get("content", "")]
    assert any("missing required args" in o for o in observations)


def test_honesty_check_challenges_false_success():
    store = InMemoryTaskStore()
    block = '```json\n{"tool": "file", "arguments": {}}\n```'
    brain = ScriptBrain([block, "all good, everything worked!",
                         "I failed: every call was denied"])
    out = run_loop(_task(store), None, store=store, brain=brain,
                   executor=FakeExecutor(
                       verdicts={"file": Verdict.DENY}), ctx=_ctx(),
                   max_stalls=9)
    assert out["status"] == "done"
    assert "failed" in out["answer"]
    challenged = [m["content"] for call in brain.calls for m in call["messages"]
                  if "Do NOT claim success" in m.get("content", "")]
    assert challenged


def test_grant_clears_approval_block():
    # ASK granted, execution fails -> terminal state is stalled (work
    # failed), not needs_approval (nothing waits on a human).
    store = InMemoryTaskStore()
    block = '```json\n{"tool": "file", "arguments": {}}\n```'
    brain = ScriptBrain([block] * 6)
    ledger = ApprovalLedger()
    ctx = _ctx(authority=ledger)
    executor = FakeExecutor(
        verdicts={"file": Verdict.ASK},
        results={"file": ToolResult(ok=False, data={}, error="boom")})
    out = run_loop(_task(store), None, store=store, brain=brain,
                   executor=executor, ctx=ctx, ledger=ledger,
                   approver=lambda info: True, max_stalls=1)
    assert out["status"] == "stalled"


def test_allowed_schemas_skip_denied():
    class Reg:
        def tools(self):
            return [{"name": "file", "description": "f",
                     "args": {"action": "read|write"}},
                    {"name": "terminal", "description": "t"}]

        def permissions(self):
            return {"workers": {"arivu": {"tools": {"file": "allow",
                                                   "terminal": "deny"}}}}

    schemas = allowed_tool_schemas("arivu", Reg())
    assert [s["name"] for s in schemas] == ["file"]
    assert schemas[0]["args"] == {"action": "read|write"}
    assert len(allowed_tool_schemas("arivu", None)) == 0

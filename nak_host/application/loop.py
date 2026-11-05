"""Agent loop: brain -> tool call -> executor -> observation -> repeat.

ReAct through prompt + parse: Mind's chat returns text (no native tool
calls), so tools are offered in the prompt and called back as fenced JSON
blocks. Every Brain call uses ``raw=True`` (our own prompt protocol) with a
TypeError fallback for older brains; history uses user/assistant/tool roles
only (Mind rejects ``system`` — the identity block goes in as ``tool``,
matching the worker chat convention).

Never raises on model/tool outcomes (status is returned); infrastructure
errors (store/transport) propagate.
"""

from __future__ import annotations

import contextlib
import json
import re
import time

from typing import Any

from nak_host.domain.permission import Verdict
from nak_host.domain.task import Task, TaskRun
from nak_host.utils.constant import LOOP_FOLLOWUP as FOLLOWUP
from nak_host.utils.constant import LOOP_FORMAT_RETRIES as FORMAT_RETRIES
from nak_host.utils.constant import LOOP_HISTORY_KEEP as HISTORY_KEEP
from nak_host.utils.constant import LOOP_MAX_CALLS_FALLBACK as MAX_CALLS_FALLBACK
from nak_host.utils.constant import LOOP_MAX_STALLS as MAX_STALLS
from nak_host.utils.constant import LOOP_OBS_CHARS as OBS_CHARS

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def render_tool_prompt(tools: Any, max_calls: int = MAX_CALLS_FALLBACK) -> str:
    """Tool catalog block for the system-equivalent message."""
    lines = ["## Tools",
             "Call tools ONLY as fenced json blocks "
             f"(at most {max_calls} per turn):",
             '```json\n{"tool": "<name>", "arguments": { ... }}\n```',
             "Example (write a file — copy this shape exactly):",
             '```json\n{"tool": "file", "arguments": {"action": "write", '
             '"path": "notes.md", "content": "hello", "mode": "create"}}\n```',
             "Every call MUST include the exact \"action\" value from the "
             "schema below. Available tools (every listed argument marked "
             "required in its hint MUST be supplied with an exact value):"]
    for entry in tools or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip()
        if not name:
            continue
        description = str(entry.get("description", "") or "").strip()
        lines.append(f"- {name}: {description or '(no description)'}")
        args = entry.get("args")
        if isinstance(args, dict) and args:
            schema = json.dumps(args, ensure_ascii=False, default=str)
            lines.append(f"  arguments: {schema[:800]}")
    lines.append("Reply WITHOUT any tool block when the work is done "
                 "(final answer).")
    lines.append("NEVER claim a tool succeeded unless its Observation says "
                 "ok — a denied/failed call did nothing. Read the Observation "
                 "reason and fix the call instead of asserting success.")
    return "\n".join(lines)


def parse_tool_calls(answer: Any) -> tuple:
    """(calls, malformed): fenced json blocks with tool+arguments."""
    calls: list[dict[str, Any]] = []
    malformed = 0
    for match in _FENCE.finditer(str(answer or "")):
        try:
            obj = json.loads(match.group(1))
        except ValueError:
            malformed += 1
            continue
        if isinstance(obj, dict) and str(obj.get("tool", "") or "").strip():
            args = obj.get("arguments", {})
            calls.append({"tool": str(obj["tool"]).strip(),
                          "arguments": dict(args) if isinstance(args, dict) else {}})
        else:
            malformed += 1
    return calls, malformed


def _verdict_name(value: Any) -> str:
    """Normalized verdict: Verdict members and exact strings; else deny."""
    if isinstance(value, Verdict):
        return value.value
    text = str(value or "").strip().lower()
    return text if text in ("allow", "ask", "deny") else "deny"


def _detail(name: str, args: dict) -> str:
    """Short evidence locator: file path or terminal command (capped)."""
    if name == "file" and isinstance(args.get("path"), str):
        return args["path"][:200]
    if name == "terminal":
        return str(args.get("command", ""))[:200]
    return ""


def allowed_tool_schemas(worker_name: str, registry: Any) -> list:
    """Tool schemas a worker may see (permission allow/ask; deny skipped)."""
    tools = registry.tools() if registry is not None else []
    try:
        perms = registry.permissions() if registry is not None else {}
    except Exception:
        perms = {}
    workers = (perms.get("workers") or {}) if isinstance(perms, dict) else {}
    entry = workers.get(str(worker_name or "")) or {}
    verdicts = entry.get("tools") or {}
    out = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name", "") or "").strip()
        if not name:
            continue
        if verdicts and str(verdicts.get(name, "deny")).lower() == "deny":
            continue
        schema: dict = {"name": name,
                         "description": str(tool.get("description", "")
                                            or "")}
        if isinstance(tool.get("args"), dict):
            schema["args"] = tool["args"]
        out.append(schema)
    return out


def _resolve_budget(value: Any, inputs: Any, key: str, default: int,
                    minimum: int = 1) -> int:
    """Resolution order: explicit kwarg > task.inputs[key] > default."""
    if value is not None:
        try:
            return max(minimum, int(value))
        except (TypeError, ValueError):
            return default
    try:
        raw = (inputs or {}).get(key, None)
    except Exception:
        raw = None
    if raw is not None:
        try:
            return max(minimum, int(raw))
        except (TypeError, ValueError):
            pass
    return default


def run_loop(task: Task, run: TaskRun, *, store: Any, brain: Any,
             executor: Any, ctx: Any, ledger: Any = None, memory: Any = None,
             tools: Any = None, identity: str = "",
             worker_name: str = "", approver: Any = None,
             session_id: Any = None, max_calls_per_turn: Any = None,
             obs_chars: Any = None, history_keep: Any = None,
             max_stalls: Any = None, auto_steps: Any = None,
             on_event: Any = None,
             clock: Any = None) -> dict[str, Any]:
    """Run one worker's tool loop. See module docstring for the protocol.

    ``ledger`` grants human approvals (defaults to a fresh ApprovalLedger;
    pass it as ``ctx.authority`` too so grants validate — run_task does this
    by construction). Returns {status, answer, steps, tool_calls, reason}.

    ``on_event`` is an optional callable(event) fired after every tool call
    (and once on finish) for live streaming. Events are plain dicts
    {step, tool, ok, verdict, detail, reason}; failures in the callback
    never break the loop. Budget kwargs resolve as explicit > task.inputs
    > built-in default so callers stay backward compatible.
    """
    from nak_host.application.approval import ApprovalLedger as _Ledger

    def _emit(event: dict[str, Any]) -> None:
        if callable(on_event):
            with contextlib.suppress(Exception):
                on_event(dict(event))

    eff_calls = _resolve_budget(max_calls_per_turn, task.inputs,
                                "max_calls_per_turn", MAX_CALLS_FALLBACK)
    eff_obs = _resolve_budget(obs_chars, task.inputs, "obs_chars", OBS_CHARS)
    eff_history = _resolve_budget(history_keep, task.inputs, "history_keep",
                                  HISTORY_KEEP)
    eff_stalls = _resolve_budget(max_stalls, task.inputs, "max_stalls",
                                 MAX_STALLS)
    eff_auto = _resolve_budget(auto_steps, task.inputs, "auto_steps", 2,
                               minimum=0)
    eff_auto = max(0, eff_auto)
    auto_used = 0
    now = clock or time.monotonic
    if ledger is None:
        ledger = _Ledger()
    start = now()
    timeout = max(1, int(task.timeout))
    max_steps = max(1, int(task.max_steps))

    who = str(worker_name or "").strip() or "worker"
    system_block = (str(identity or "").strip()
                    or f"You are {who}, a coding worker. Work step by step.")
    # Worker prompts carry plan/approval autonomy text ("propose, then STOP").
    # Inside the loop that text is wrong: execution is already approved, so
    # override it explicitly — act now, never plan-and-wait in prose.
    system_block += ("\nYou are EXECUTING now: make tool calls immediately "
                     "instead of proposing plans or narrating next steps. "
                     "Approval gates are handled by the system — never stop "
                     "to ask the user for permission in text; keep calling "
                     "tools until the work is done.")
    history: list[dict[str, str]] = [
        {"role": "tool", "content": system_block},
        {"role": "tool", "content": render_tool_prompt(tools, eff_calls)},
        {"role": "user", "content": task.goal},
    ]
    if memory is not None:
        try:
            recalled = memory.recall(task.goal)
            hits = (recalled or {}).get("hits", []) if isinstance(recalled, dict) else []
            if hits:
                blob = json.dumps(hits, ensure_ascii=False, default=str)[:eff_obs]
                history.append({"role": "tool",
                                "content": f"Recalled context:\n{blob}"})
        except Exception:
            pass

    def _chat(text: str) -> Any:
        try:
            return brain.chat(text, messages=list(history),
                              session_id=session_id, raw=True)
        except TypeError:
            try:
                return brain.chat(text, messages=list(history),
                                  session_id=session_id)
            except TypeError:
                return brain.chat(text, messages=list(history))

    def _finish(status: str, answer: str, steps: int,
                calls: list, reason: str = "") -> dict[str, Any]:
        with contextlib.suppress(Exception):
            store.append_event(task.id, {"type": "task.loop_finished",
                                         "status": status, "steps": steps,
                                         "tool_calls": len(calls),
                                         "reason": reason})
        _emit({"type": "loop_finished", "status": status, "steps": steps,
               "tool_calls": len(calls), "reason": str(reason or "")})
        if memory is not None:
            with contextlib.suppress(Exception):
                memory.store(text=f"Task {task.id} finished {status} after "
                                  f"{steps} steps: {str(answer or '')[:400]}")
        return {"status": status, "answer": str(answer or ""), "steps": steps,
                "tool_calls": calls, "reason": reason}

    with contextlib.suppress(Exception):
        store.append_event(task.id, {"type": "task.loop_started",
                                     "worker": who})

    granted_ctx = ctx
    granted = False
    stalls = 0
    approval_blocked = False
    honesty_done = False
    format_left = FORMAT_RETRIES
    calls_log: list = []
    answer = ""
    detail_fn = getattr(executor, "inspect_detail", None)

    def _check(name: str, args: dict, eff_ctx: Any) -> tuple:
        """(verdict, reason): explainable inspect when the executor offers it."""
        if callable(detail_fn):
            try:
                verdict, reason = detail_fn(name, args, eff_ctx)
                return _verdict_name(verdict), str(reason or "")[:300]
            except Exception:
                pass
        return _verdict_name(executor.inspect(name, args, eff_ctx)), ""
    for step in range(1, max_steps + 1):
        try:
            ctx.cancel.raise_if_cancelled()
        except Exception as exc:
            return _finish("cancelled", answer, step - 1, calls_log,
                           f"cancelled: {exc}")
        if now() - start > timeout:
            return _finish("timeout", answer, step - 1, calls_log,
                           f"wall-clock budget {timeout}s exceeded")
        text = task.goal if step == 1 else FOLLOWUP
        try:
            resp = _chat(text)
        except Exception as exc:
            return _finish("failed", answer, step - 1, calls_log,
                           f"brain call failed: {exc}")
        answer = str((resp or {}).get("answer", "") or "")
        history.append({"role": "assistant", "content": answer})
        calls, malformed = parse_tool_calls(answer)
        if not calls:
            if malformed and format_left > 0:
                format_left -= 1
                history.append({"role": "user",
                                "content": "No valid tool block found. "
                                           "Emit fenced json "
                                           '{"tool": ..., "arguments": {...}} '
                                           "or reply with the final answer."})
                continue
            if (not honesty_done and calls_log
                    and not any(c.get("ok") for c in calls_log)):
                honesty_done = True
                history.append({"role": "user",
                                "content": "None of your tool calls succeeded "
                                           "(all were denied, failed or blocked). "
                                           "Do NOT claim success. Either fix the calls "
                                           "using the Observation reasons, or reply with "
                                           "an honest failure report (no tool block)."})
                continue
            return _finish("done", answer, step, calls_log)
        progressed = False
        for call in calls[:max(1, eff_calls)]:
            name, args = call["tool"], call["arguments"]
            verdict, reason = _check(name, args, granted_ctx)
            if verdict == "deny":
                note = f": {reason}" if reason else ""
                history.append({"role": "tool",
                                "content": f"## Observation ({name}): "
                                           f"denied by policy{note}"})
                calls_log.append({"step": step, "tool": name, "ok": False,
                                  "verdict": "deny",
                                  "detail": _detail(name, args),
                                  "reason": str(reason or "")})
                _emit({"step": step, "tool": name, "ok": False,
                       "verdict": "deny", "detail": _detail(name, args),
                       "reason": str(reason or "")})
                continue
            if verdict == "ask" and not granted:
                approval_blocked = True
                approved = False
                auto = False
                if auto_used < eff_auto:
                    approved = True
                    auto = True
                else:
                    need_note = f": {reason}" if reason else " (no approver)"
                    if callable(approver):
                        try:
                            approved = bool(approver({"task": task.id,
                                                      "worker": who,
                                                      "tool": name,
                                                      "arguments": args,
                                                      "step": step}))
                        except Exception:
                            approved = False
                    if not approved:
                        history.append({"role": "tool",
                                        "content": f"## Observation ({name}): "
                                                   f"needs approval{need_note}"})
                        calls_log.append({"step": step, "tool": name,
                                          "ok": False, "verdict": "ask",
                                          "detail": _detail(name, args),
                                          "reason": str(reason or "")})
                        _emit({"step": step, "tool": name, "ok": False,
                               "verdict": "ask", "detail": _detail(name, args),
                               "reason": str(reason or "")})
                        continue
                from nak_host.application.approval import mint_approval as _mint

                granted_ctx = ledger.derive(
                    ctx, ledger.grant(_mint(scope="task",
                                            granted_by="auto" if auto
                                            else "human")))
                granted = True
                approval_blocked = False
                if auto:
                    auto_used += 1
                with contextlib.suppress(Exception):
                    store.append_event(task.id, {"type": "task.approval_granted",
                                                 "tool": name,
                                                 "auto": bool(auto)})
            try:
                result = executor.run(name, args, granted_ctx)
            except Exception as exc:
                history.append({"role": "tool",
                                "content": f"## Observation ({name}): "
                                           f"error: {exc}"})
                calls_log.append({"step": step, "tool": name, "ok": False,
                                  "verdict": "error",
                                  "detail": _detail(name, args),
                                  "reason": str(exc)[:300]})
                _emit({"step": step, "tool": name, "ok": False,
                       "verdict": "error", "detail": _detail(name, args),
                       "reason": str(exc)[:300]})
                continue
            ok = bool(getattr(result, "ok", False))
            data = getattr(result, "data", {})
            error = getattr(result, "error", "")
            body = (json.dumps(data, ensure_ascii=False, default=str)
                    if ok else f"failed: {error}")
            history.append({"role": "tool",
                            "content": f"## Observation ({name}):\n{body[:eff_obs]}"})
            entry: dict[str, Any] = {"step": step, "tool": name, "ok": ok,
                                     "verdict": "allow",
                                     "detail": _detail(name, args),
                                     "reason": "" if ok else str(error)[:300]}
            if str(name) == "terminal":
                entry["output"] = str(body)[:500]
            calls_log.append(entry)
            _emit({"step": step, "tool": name, "ok": ok,
                   "verdict": "allow", "detail": _detail(name, args),
                   "reason": entry["reason"]})
            progressed = progressed or ok
        history = history[:2] + history[-(max(4, eff_history)):] \
            if len(history) > max(4, eff_history) + 2 else history
        if progressed:
            stalls = 0
            approval_blocked = False
        else:
            stalls += 1
            if stalls > eff_stalls:
                if approval_blocked:
                    return _finish("needs_approval", answer, step, calls_log,
                                   "waiting on approval")
                return _finish("stalled", answer, step, calls_log,
                               f"{stalls} turns without progress")
    return _finish("max_steps", answer, max_steps, calls_log,
                   f"step budget {max_steps} exceeded")

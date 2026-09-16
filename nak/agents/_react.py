"""nak.agents._react -- shared Mind-only ReAct loop (Phase 6).

Canonical implementation of the agent tool loop, declared in
nak/agents/shared.json under the name "_react". Hand-written specialists
(Coding / Testing / Review) AND every Genesis-generated agent run this
instead of copy-pasting the tool loop:

    context -> Mind step as strict JSON {tool,arguments}|{answer} -> policy
    gate -> execute_plugin -> transcript -> answer or max_turns.

Legacy agents (Planning / Research) predate it and keep their own aligned
copies — do not refactor them onto this helper without updating their
offline tests.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _extract_first_json_object(text: str):
    """Balanced-brace scan for the first complete JSON object in free text.

    The old first-`{`-to-last-`}` slice breaks when the model appends
    commentary (especially text containing braces) after the tool call.
    String-aware so braces inside quoted code don't confuse depth.
    Returns the parsed dict or None.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start:i + 1])
                        except ValueError:
                            break
                        return obj if isinstance(obj, dict) else None
        start = text.find("{", start + 1)
    return None


def parse_step_answer(raw) -> Dict[str, Any]:
    """Mind reply -> answer-step or tool-step. Unparseable replies become final answers."""
    answer = ""
    if isinstance(raw, dict):
        answer = str(raw.get("answer") or raw.get("content") or raw.get("text") or "")
    else:
        answer = str(raw)
    text = answer.strip()
    data = None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = _extract_first_json_object(text)
    if isinstance(data, dict):
        if "answer" in data and "tool" not in data:
            return {"kind": "answer", "text": str(data["answer"])}
        if "tool" in data:
            args = data.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = {}
            if not isinstance(args, dict):
                args = {}
            return {"kind": "tool", "name": str(data["tool"]), "args": args}
    return {"kind": "answer", "text": answer}


def run_react_loop(
    *,
    brain,
    agent_name: str,
    role_hint: str,
    text: str,
    custom_instructions: str = "",
    messages: Optional[List[Dict[str, str]]] = None,
    session_id: Optional[str] = None,
    mind_step=None,
) -> Dict[str, Any]:
    """Run the standard tool loop. `mind_step(prompt, messages, session_id)` injectable for tests."""
    from nak.plugins import PLUGIN_TOOLS, execute_plugin
    try:
        from nak.plugins.tool.manifest import manifest_specs as _manifest_specs
        _SPECS = _manifest_specs() or list(PLUGIN_TOOLS)
    except Exception:
        _SPECS = list(PLUGIN_TOOLS)
    from nak.plugins.tool.policy import reset_tool_once, approve_tool_use
    from nak.utils.config import load_config

    if not text or not text.strip():
        raise ValueError("text must be non-empty")
    context = " ".join(
        p.strip() for p in (role_hint, custom_instructions) if p and p.strip()
    ).strip()
    effective = f"Agent context: {context}\nUser request: {text.strip()}" if context else text.strip()
    history = [m for m in (messages or []) if m.get("role") in ("user", "assistant", "tool")]
    try:
        max_turns = max(1, load_config().policy_max_turns)
    except Exception:
        max_turns = 6
    reset_tool_once()
    tool_brief = "; ".join(
        s["function"]["name"] + ": " + s["function"].get("description", "")[:80]
        for s in _SPECS
    )
    step_fn = mind_step or (lambda prompt, msgs, sid: brain.chat(prompt, messages=msgs or None, session_id=sid))
    transcript = [{"role": "user", "content": effective}]

    def _arg_preview(args: Any) -> str:
        # Truncated args ride along in steps[] so post-hoc traces (state.json)
        # show WHAT ran, not just THAT something ran. AgentExecutor mirrors
        # these into HarnessState verbatim.
        try:
            text = json.dumps(args, ensure_ascii=False, default=str)
        except Exception:
            text = str(args)[:300]
        return text if len(text) <= 300 else text[:300] + "…(truncated)"

    last_text, turns, steps = "", 0, []
    for _ in range(max_turns):
        turns += 1
        step_prompt = (
            "Reply with ONLY JSON - either " +
            '{"tool": "<name>", "arguments": {...}}' +
            " to use one tool, or " +
            '{"answer": "..."}' +
            " when done. No other text.\n" +
            "Available tools: " + tool_brief + "\n" +
            "History so far:\n" + json.dumps(transcript[-10:], ensure_ascii=False)[:6000]
        )
        try:
            raw = step_fn(step_prompt, (history + transcript)[-12:] or None, session_id)
        except Exception as exc:
            last_text = f"[brain error: {exc}]"
            break
        step = parse_step_answer(raw)
        if step["kind"] == "answer":
            last_text = step["text"]
            break
        tname, targs = step["name"], step["args"]
        if approve_tool_use(tname, targs):
            try:
                result = execute_plugin(brain, tname, targs)
                try:
                    rok = "error" not in json.loads(result)
                except (ValueError, TypeError):
                    rok = True
                steps.append({"tool": tname, "ok": rok, "args": _arg_preview(targs)})
            except Exception as exc:
                result = json.dumps({"error": str(exc)[:500]})
                steps.append({"tool": tname, "ok": False, "args": _arg_preview(targs)})
        else:
            result = json.dumps({"error": f"tool {tname!r} use denied (policy/user)"})
            steps.append({"tool": tname, "ok": False, "denied": True, "args": _arg_preview(targs)})
        transcript.append({"role": "assistant",
                           "content": json.dumps({"tool": tname, "arguments": targs}, ensure_ascii=False)})
        transcript.append({"role": "tool", "content": result})
    answer = last_text or "(no answer after tool loop)"
    return {"answer": answer, "turns": turns, "steps": steps, "agent": agent_name}


__all__ = ["parse_step_answer", "run_react_loop"]

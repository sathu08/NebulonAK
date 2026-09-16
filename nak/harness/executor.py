"""nak.harness.executor -- central agent + tool execution (Phase 1 + 2).

Before: 3 divergent `run_sync` copies in Planning/Research/Genesis +
process-global policy flags + repo-root file sandbox.

After: one AgentExecutor (agent resolution + run) and one ToolExecutor
(policy-gated, workspace-scoped, state-recording). Agents keep their own
LLM loop for now; the harness records every step into HarnessState so the
full trace is inspectable.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from nak.plugins import execute_plugin

logger = logging.getLogger("nak.harness.executor")


class AgentExecutor:
    """Resolve + run agents. Single copy of the logic pipeline.py had."""

    def __init__(self, brain) -> None:
        self.brain = brain

    def is_resolvable(self, agent_name: str) -> bool:
        if not agent_name:
            return False
        try:
            mod = importlib.import_module(f"nak.agents.{agent_name}.agent")
            if getattr(mod, "Agent", None) is not None:
                return True
        except ModuleNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            return False
        return False

    async def run_text(
        self,
        agent_name: str,
        text: str,
        session_id: Optional[str] = None,
        state=None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run a resolved agent. STRICT: raises FileNotFoundError if missing.

        When `state` carries a workspace, it becomes the ambient file sandbox
        for the whole agent run (agent-internal tool calls stay isolated).
        """
        from nak.plugins.tool.workspace import set_current_workspace

        if not self.is_resolvable(agent_name):
            raise FileNotFoundError(
                f"agent {agent_name!r} is not installed "
                f"(missing nak/agents/{agent_name}/agent.py)"
            )
        ws = getattr(state, "workspace", None) if state is not None else None
        with set_current_workspace(ws):
            return await self._run_text_inner(agent_name, text, session_id, state, **kwargs)

    async def _run_text_inner(
        self,
        agent_name: str,
        text: str,
        session_id: Optional[str] = None,
        state=None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            mod = importlib.import_module(f"nak.agents.{agent_name}.agent")
        except ModuleNotFoundError:
            mod = None  # type: ignore
        if mod is not None and getattr(mod, "Agent", None) is not None:
            cls = getattr(mod, "Agent")
            inst = cls(brain=self.brain)
            if hasattr(inst, "run") and asyncio.iscoroutinefunction(
                getattr(inst, "run")
            ):
                data = await inst.run(text, session_id=session_id, **kwargs)
            elif hasattr(inst, "run_sync"):
                data = await asyncio.to_thread(
                    inst.run_sync, text, session_id=session_id, **kwargs
                )
            elif hasattr(inst, "run"):
                data = await asyncio.to_thread(
                    inst.run, text, session_id=session_id, **kwargs
                )
            else:
                raise AttributeError(f"Agent {agent_name} has no run/run_sync")
            answer = str(
                data.get("answer") or data.get("content") or data.get("text") or ""
            )
            steps = data.get("steps") if isinstance(data, dict) else None
            step_list = list(steps) if isinstance(steps, list) else []
            if state is not None:
                for s in step_list:
                    try:
                        denied = bool(s.get("denied", False))
                        ok = bool(s.get("ok", True))
                        verdict = "NEEDS_USER" if denied else ("SUCCESS" if ok else "FAILURE")
                        preview = s.get("args", "")
                        mirrored = {"preview": str(preview)[:300]} if preview else {}
                        state.record_tool(
                            str(s.get("tool", "?")),
                            mirrored,
                            ok=ok,
                            denied=denied,
                            classification=verdict,
                        )
                    except Exception:  # noqa: BLE001
                        pass
            return {
                "answer": answer,
                "run_on": agent_name,
                "raw": data,
                "steps": step_list,
            }
        raise AttributeError(f"Agent {agent_name} has no Agent class "
                             f"(missing nak/agents/{agent_name}/agent.py)")


class ToolExecutor:
    """Policy-gated tool calls scoped to one HarnessState + workspace."""

    # tools whose file args resolve under the workspace root
    _FILE_SCOPED = {
        "read_file", "write_file", "edit_file", "list_files", "search_files",
    }
    # tools that execute with cwd = workspace project dir
    _EXEC_SCOPED = {"shell", "python_exec", "run_test", "run_build"}

    def __init__(self, brain, workspace_root: Optional[str | Path] = None) -> None:
        self.brain = brain
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root
            else None
        )

    def execute(
        self,
        state,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Approve (per-state policy) -> run -> classify -> record. Returns JSON string."""
        from nak.plugins.tool.policy import approve_tool_use_for_state

        from .events import log_event
        from .observer import inspect as classify

        args = dict(args or {})
        if not approve_tool_use_for_state(state, tool_name, args):
            if state is not None:
                state.record_tool(tool_name, args, ok=False, denied=True,
                                  classification="NEEDS_USER")
                log_event(state, "tool_result", f"{tool_name} denied (policy/user)",
                          classification="NEEDS_USER")
            return json.dumps({"error": f"tool {tool_name!r} use denied (policy/user)"})

        # workspace scoping: file tools get _root, exec tools get _cwd
        ws = None
        if state is not None and getattr(state, "workspace", None):
            ws = getattr(state, "workspace")
        elif self.workspace_root is not None:
            ws = str(self.workspace_root)
        kwargs: Dict[str, Any] = {}
        if tool_name in self._FILE_SCOPED and ws:
            kwargs["_root"] = ws
        if tool_name in self._EXEC_SCOPED and ws:
            kwargs["_cwd"] = ws

        try:
            result = execute_plugin(self.brain, tool_name, args, **kwargs)
            verdict = classify(result)
            status, reason = verdict["status"], verdict["reason"]
            rok = status in ("SUCCESS", "PARTIAL_SUCCESS")
            err = "" if rok else reason
            if state is not None:
                state.record_tool(tool_name, args, ok=rok, error=err,
                                  classification=status)
                log_event(state, "tool_result",
                          f"{tool_name} {status}: {reason[:200]}",
                          classification=status)
            return result
        except Exception as exc:  # noqa: BLE001
            if state is not None:
                state.record_tool(tool_name, args, ok=False, error=str(exc)[:500],
                                  classification="FAILURE")
                log_event(state, "tool_result", f"{tool_name} exception: {exc}",
                          classification="FAILURE")
            return json.dumps({"error": str(exc)[:500]})


__all__ = ["AgentExecutor", "ToolExecutor"]

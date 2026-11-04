"""Harness executor adapter: ToolExecutorPort over ExecutionHarness.

Translation only — all policy lives in the harness engine:

* ``inspect`` -> ``Verdict`` (ok -> ALLOW, need_approval -> ASK, else DENY);
* ``run``    -> ``ToolResult``; ASK/DENY become ``ok=False`` results (never
  raised), while a revoked approval, cancellation, or transport failure
  raises.

Approval semantics: the harness approver is bound to
``ctx.authority.is_valid(ctx.approval, ctx)``, so the authority is
re-consulted at execution time (TOCTOU-safe). An ASK outcome with no usable
approval returns ``need_approval`` in the result; an ASK with a present but
invalid ref raises ApprovalRevoked. Tools that need no approval never consult
the authority.
"""

from __future__ import annotations

import json

from typing import Any

from nak.domain.errors import ApprovalRevoked
from nak.domain.ports import ExecutionContext, ToolResult
from nak.domain.permission import Verdict


class HarnessExecutor:
    """Execute tool calls under an ExecutionContext (harness injected)."""

    def __init__(self, harness: Any = None) -> None:
        if harness is None:
            raise ValueError("harness is required — build via make_executor()")
        self._harness = harness

    def _allow(self, ctx: ExecutionContext) -> list | None:
        tools = sorted({str(t or "").strip() for t in (ctx.allowed_tools or ())
                        if str(t or "").strip()})
        return tools or None  # empty = no task-level restriction (harness policy applies)

    def _plan(self, tool: str, args: dict[str, Any],
              ctx: ExecutionContext) -> tuple[Verdict, str]:
        """Verdict + human-readable reason (approver never consulted here)."""
        name = str(tool or "").strip()
        if not name:
            return Verdict.DENY, "missing tool name"
        try:
            res = self._harness.inspect(name, dict(args or {}),
                                        allow=self._allow(ctx),
                                        worker=ctx.worker_id,
                                        approver=None)
        except Exception as exc:
            return Verdict.DENY, f"inspect failed: {exc}"
        if not isinstance(res, dict):
            return Verdict.DENY, "inspect returned no plan"
        if res.get("ok"):
            return Verdict.ALLOW, ""
        if res.get("need_approval"):
            return Verdict.ASK, str(res.get("error", "") or "needs approval")
        return Verdict.DENY, str(res.get("error", "") or "denied")

    def inspect(self, tool: str, args: dict[str, Any],
                ctx: ExecutionContext) -> Verdict:
        """Check a tool call under the context (no execution)."""
        verdict, _ = self._plan(tool, args, ctx)
        return verdict

    def inspect_detail(self, tool: str, args: dict[str, Any],
                       ctx: ExecutionContext) -> tuple:
        """(Verdict, reason): the explainable form of inspect().

        Extra method beyond ToolExecutorPort (which stays minimal): loops use
        the reason to tell the model HOW to fix a denied call (missing args,
        approval needed, ...). Reasons carry no secrets.
        """
        verdict, reason = self._plan(tool, args, ctx)
        return verdict, str(reason or "")[:300]

    def run(self, tool: str, args: dict[str, Any],
            ctx: ExecutionContext) -> ToolResult:
        """Execute a tool call under the context."""
        ctx.cancel.raise_if_cancelled()
        verdict, reason = self._plan(tool, args, ctx)
        if verdict == Verdict.DENY:
            return ToolResult(ok=False, data={"tool": str(tool or "")},
                              error=reason or "denied")
        if verdict == Verdict.ASK and ctx.approval is None:
            return ToolResult(ok=False, data={"tool": str(tool or ""),
                                              "need_approval": True},
                              error=reason or "needs approval")
        if verdict == Verdict.ASK and not ctx.authority.is_valid(ctx.approval, ctx):
            raise ApprovalRevoked(
                f"approval {getattr(ctx.approval, 'id', '?')!r} is no longer valid")
        try:
            raw = self._harness.run(
                str(tool or "").strip(), dict(args or {}),
                allow=self._allow(ctx), worker=ctx.worker_id,
                approver=lambda *a, **k: bool(
                    ctx.authority.is_valid(ctx.approval, ctx)))
        except Exception as exc:
            return ToolResult(ok=False, data={"tool": str(tool or "")},
                              error=f"tool run failed: {exc}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return ToolResult(ok=False, data={"tool": str(tool or ""),
                                              "raw": str(raw)[:500]},
                              error="non-JSON tool output")
        if not isinstance(parsed, dict):
            return ToolResult(ok=False, data={"tool": str(tool or "")},
                              error="tool returned no result object")
        ok = bool(parsed.get("ok"))
        data = {k: v for k, v in parsed.items() if k != "ok"}
        if not ok and parsed.get("need_approval"):
            data.setdefault("need_approval", True)
        return ToolResult(ok=ok, data=data,
                          error="" if ok else str(parsed.get("error", "") or "failed"))

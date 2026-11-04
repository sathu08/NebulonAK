"""nak.harness.state -- explicit execution state (Phase 1).

The framework kept state implicit inside each agent's tool loop. The harness
owns the lifecycle, so state is a first-class dataclass that can be
inspected, logged, and persisted to workspace/state.json:

    REQUEST -> DECISION -> PLAN -> STEP -> OBSERVATION -> VERIFY -> COMPLETE
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


STATUS_RUNNING = "running"
STATUS_NEEDS_CONFIRM = "needs_confirm"
STATUS_WAITING_USER = "waiting_user"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


@dataclass
class ToolCallRecord:
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    denied: bool = False
    error: str = ""
    classification: str = ""  # Phase 3: observer verdict (SUCCESS/FAILURE/...)


@dataclass
class Observation:
    step: int
    kind: str  # e.g. tool_result, decision, error, verify
    summary: str


@dataclass
class HarnessState:
    """One task's full execution trace."""

    request: str
    session_id: Optional[str] = None
    state_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent: Optional[str] = None
    decision: Dict[str, Any] = field(default_factory=dict)
    plan: List[str] = field(default_factory=list)
    current_step: int = 0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    verification_results: List[Dict[str, Any]] = field(default_factory=list)
    recovery_attempts: int = 0  # Phase 5: bounded auto-recovery budget counter
    status: str = STATUS_RUNNING
    turn: int = 0
    workspace: Optional[str] = None
    permissions: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, str]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    # -- mutation helpers -------------------------------------------------

    def next_turn(self) -> int:
        self.turn += 1
        return self.turn

    def record_tool(
        self,
        tool: str,
        args: Dict[str, Any],
        ok: bool = True,
        denied: bool = False,
        error: str = "",
        classification: str = "",
    ) -> None:
        self.tool_calls.append(
            asdict(ToolCallRecord(tool=tool, args=args, ok=ok, denied=denied,
                                 error=error, classification=classification))
        )
        self.current_step += 1

    def record_observation(self, kind: str, summary: str) -> None:
        self.observations.append(
            asdict(Observation(step=self.current_step, kind=kind, summary=summary[:1000]))
        )

    def record_error(self, message: str) -> None:
        self.errors.append(str(message)[:1000])
        self.record_observation("error", str(message)[:500])

    def finish(self, status: str = STATUS_DONE) -> None:
        self.status = status

    def fail(self, message: str) -> None:
        self.record_error(message)
        self.status = STATUS_FAILED

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HarnessState":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def create_state(
    request: str,
    session_id: Optional[str] = None,
    workspace: Optional[str] = None,
) -> HarnessState:
    """Factory: fresh running state for one user request."""
    if not request or not request.strip():
        raise ValueError("request must be non-empty")
    return HarnessState(
        request=request.strip(),
        session_id=session_id,
        workspace=workspace,
        permissions={"tool_once_ok": False},
        history=[{"role": "user", "content": request.strip()}],
    )


__all__ = [
    "HarnessState",
    "ToolCallRecord",
    "Observation",
    "create_state",
    "STATUS_RUNNING",
    "STATUS_NEEDS_CONFIRM",
    "STATUS_WAITING_USER",
    "STATUS_DONE",
    "STATUS_FAILED",
]

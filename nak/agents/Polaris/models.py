"""nak.agents.Polaris.models -- structured decision result."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

Action = Literal["USE_AGENT", "CREATE_AGENT", "ASK_USER"]
VALID_ACTIONS = {"USE_AGENT", "CREATE_AGENT", "ASK_USER"}


@dataclass
class DecisionResult:
    action: Action
    agent_name: Optional[str] = None
    reason: str = ""
    confidence: float = 0.0
    parameters: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"invalid action {self.action!r}, must be one of {VALID_ACTIONS}")
        # normalize confidence
        try:
            self.confidence = float(self.confidence)
        except Exception:
            self.confidence = 0.0
        self.confidence = max(0.0, min(1.0, self.confidence))
        # normalize agent_name
        if self.agent_name is not None:
            n = str(self.agent_name).strip()
            self.agent_name = n if n and n.lower() != "null" else None
            if self.agent_name == "":
                self.agent_name = None
        if not isinstance(self.parameters, dict):
            self.parameters = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "agent_name": self.agent_name,
            "reason": self.reason,
            "confidence": self.confidence,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionResult":
        return cls(
            action=str(d.get("action", "ASK_USER")).strip().upper(),  # type: ignore
            agent_name=d.get("agent_name"),
            reason=str(d.get("reason", "") or ""),
            confidence=float(d.get("confidence", 0.0) or 0.0),
            parameters=dict(d.get("parameters") or {}),
        )

    @property
    def is_use(self) -> bool:
        return self.action == "USE_AGENT"

    @property
    def is_create(self) -> bool:
        return self.action == "CREATE_AGENT"

    @property
    def is_ask(self) -> bool:
        return self.action == "ASK_USER"

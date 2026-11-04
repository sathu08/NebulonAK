"""Report-card data models and shared text helpers."""
from __future__ import annotations

import secrets

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SECTIONS = ("persona", "behavior", "tools", "craft")
DEFAULT_WEIGHTS = {"persona": 25.0, "behavior": 25.0, "tools": 25.0, "craft": 25.0}


@dataclass
class Check:
    section: str
    name: str
    question: str
    expected: str
    actual: str = ""
    score: float | None = 0.0
    status: str = "fail"
    detail: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Section:
    name: str
    checks: list[Check] = field(default_factory=list)
    score: float | None = None
    grade: str = "N/A"
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReportCard:
    worker: str
    run_dir: str = ""
    profile: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, Section] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    overall: float = 0.0
    grade: str = "F"
    passed: bool = False
    threshold: float = 60.0
    error: str = ""
    session_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    suggestions: list[str] = field(default_factory=list)
    card_md: str = ""
    card_json: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("card_md", None)
        data.pop("card_json", None)
        return data


@dataclass
class ReportSummary:
    run_dir: str
    report_dir: str
    started_at: str
    finished_at: str = ""
    cards: list[ReportCard] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    exit_code: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": self.run_dir,
            "report_dir": self.report_dir,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cards": [card.to_dict() for card in self.cards],
            "skipped": list(self.skipped),
            "exit_code": self.exit_code,
            "error": self.error,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_"
                   for ch in str(name or "").strip()).strip("._") or "worker"


def _default_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (Path(__file__).resolve().parent.parent / "runs"
            / f"{stamp}_{secrets.token_hex(3)}")


def _grade(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _text(value: Any, default: str = "not specified") -> str:
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts) or default
    return str(value or "").strip() or default


def _craft_names(value: Any) -> str:
    if not isinstance(value, (list, tuple)):
        return _text(value)
    names = []
    for item in value:
        name = (str(item.get("name", "") or "").strip()
                if isinstance(item, dict) else str(item or "").strip())
        if name:
            names.append(name)
    return ", ".join(names) or "not specified"


def _autonomy_text(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "not specified"
    parts = []
    for key in ("modes", "max_steps", "approval", "auto_steps"):
        if key in value:
            parts.append(f"{key}={_text(value.get(key))}")
    return "; ".join(parts) or "not specified"


"""Data models for the vetting pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Case:
    id: str
    question: str
    expected: str
    rubric: str
    difficulty: str
    weight: float = 1.0
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Answer:
    case_id: str
    text: str = ""
    error: str = ""
    latency_ms: int = 0
    chars: int = 0
    truncated: bool = False
    triage: dict[str, Any] = field(default_factory=dict)
    policy_action: str = ""
    blocked: bool = False
    blocked_by_policy: bool = False
    inconclusive: bool = False
    inconclusive_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Judgement:
    case_id: str
    score: float = 0.0
    rationale: str = ""
    evidence: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProbeResult:
    name: str
    kind: str
    status: str
    detail: str = ""
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Improvement:
    id: str
    worker: str
    source: str = ""
    section: str = ""
    severity: str = "medium"
    problem: str = ""
    evidence: str = ""
    suggestion: str = ""
    scope: str = "worker"
    target: str = ""
    affected_workers: list[str] = field(default_factory=list)
    status: str = "open"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkerResult:
    name: str
    profile: dict[str, Any] = field(default_factory=dict)
    cases: list[Case] = field(default_factory=list)
    answers: list[Answer] = field(default_factory=list)
    judgements: list[Judgement] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    improvements: list[Improvement] = field(default_factory=list)
    tool_probes: list[ProbeResult] = field(default_factory=list)
    craft_probes: list[ProbeResult] = field(default_factory=list)
    score: float = 0.0
    threshold: float = 0.6
    passed: bool = False
    status: str = ""
    error: str = ""
    session_id: str = ""
    transcript_path: str = ""
    nunnan_history_path: str = ""
    report_md: str = ""
    report_json: str = ""
    structural: dict[str, float] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunSummary:
    run_dir: str
    started_at: str
    finished_at: str = ""
    cases: int = 6
    threshold: float = 0.6
    workers: list[WorkerResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    exit_code: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProbeConfig:
    max_cases: int = 6
    answer_chars: int = 8000
    difficulty_counts: dict[str, int] | None = None

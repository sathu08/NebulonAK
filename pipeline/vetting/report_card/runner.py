"""Report-card orchestration."""
from __future__ import annotations

import json

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nak_host.harness.engine.permission import (
    load_worker_names,
)

from ..utils.runner import (
    build_profile,
)
from .checks import (
    _ASSESSOR,
    _behavior_checks,
    _craft_checks,
    _persona_checks,
    _tool_checks,
)
from .models import (
    DEFAULT_WEIGHTS,
    ReportCard,
    ReportSummary,
    _default_run_dir,
    _grade,
    _now,
    _safe_name,
)
from .render import (
    _write_json,
    _write_text,
    render_card_md,
    render_summary_md,
)
from .scoring import (
    _make_section,
    _overall,
    _overall_suggestions,
)

def run_report_card(
    workers: list[str] | None = None,
    run_dir: str | Path | None = None,
    *,
    workers_file: Any = None,
    permissions_file: Any = None,
    endpoint: str | None = None,
    timeout: float | None = None,
    persona_cases: int = 10,
    behavior_cases: int = 10,
    threshold: float = 60.0,
    weights: dict[str, float] | None = None,
    answer_chars: int = 4000,
    brain: Any = None,
    nunnan_brain: Any = None,
    worker_factory: Callable[[str, Any], Any] | None = None,
    nunnan_factory: Callable[[Any], Any] | None = None,
    include_assessor: bool = False,
    probe_crafts_enabled: bool = True,
    probe_tools_enabled: bool = True,
) -> ReportSummary:
    if isinstance(workers, str):
        workers = [part.strip() for part in workers.split(",") if part.strip()]
    run_path = Path(run_dir) if run_dir else _default_run_dir()
    run_path.mkdir(parents=True, exist_ok=True)
    report_dir = run_path / "report_card"
    report_dir.mkdir(parents=True, exist_ok=True)
    started = _now()
    summary = ReportSummary(
        run_dir=str(run_path), report_dir=str(report_dir), started_at=started)
    known = load_worker_names(workers_file)
    if workers:
        names = list(dict.fromkeys(
            str(worker).strip() for worker in workers if str(worker).strip()))
        unknown = [worker for worker in names if known and worker not in known]
        if unknown:
            summary.error = f"unknown worker(s): {unknown}; have: {known}"
            summary.exit_code = 2
            summary.finished_at = _now()
            _write_text(report_dir / "summary.md", render_summary_md(summary))
            _write_json(report_dir / "summary.json", summary.to_dict())
            return summary
    else:
        names = list(known)
        if not include_assessor and _ASSESSOR in names:
            names = [name for name in names if name != _ASSESSOR]
            summary.skipped.append(
                f"{_ASSESSOR}: assessor excluded from auto-run "
                f"(pass --include-assessor to test it)")
    if not names:
        summary.error = "no workers to test"
        summary.exit_code = 2
        summary.finished_at = _now()
        _write_text(report_dir / "summary.md", render_summary_md(summary))
        _write_json(report_dir / "summary.json", summary.to_dict())
        return summary
    card_weights = dict(DEFAULT_WEIGHTS)
    if weights:
        card_weights.update({str(key).lower(): float(value)
                             for key, value in weights.items()})
    for name in names:
        card = ReportCard(
            worker=name,
            run_dir=str(run_path),
            threshold=float(threshold),
            weights=dict(card_weights),
            session_id=f"reportcard-{run_path.name}-{_safe_name(name)}",
            started_at=_now(),
        )
        try:
            card.profile = build_profile(name, workers_file, permissions_file)
            sections = {}
            if persona_cases > 0:
                sections["persona"] = _make_section("persona", _persona_checks(
                    name, card.profile, run_path,
                    endpoint=endpoint, timeout=timeout,
                    workers_file=workers_file, brain=brain,
                    nunnan_brain=nunnan_brain, worker_factory=worker_factory,
                    nunnan_factory=nunnan_factory, cases=persona_cases,
                    answer_chars=answer_chars,
                ))
            if behavior_cases > 0:
                sections["behavior"] = _make_section("behavior", _behavior_checks(
                    name, card.profile, run_path,
                    case_count=behavior_cases,
                    endpoint=endpoint, timeout=timeout,
                    workers_file=workers_file, brain=brain,
                    nunnan_brain=nunnan_brain, worker_factory=worker_factory,
                    nunnan_factory=nunnan_factory, answer_chars=answer_chars,
                ))
            if probe_tools_enabled:
                sections["tools"] = _make_section("tools", _tool_checks(
                    name, report_dir / "probes" / _safe_name(name),
                    workers_file=workers_file,
                    permissions_file=permissions_file,
                ))
            if probe_crafts_enabled:
                sections["craft"] = _make_section("craft", _craft_checks(
                    name, workers_file=workers_file,
                    permissions_file=permissions_file,
                ))
            card.sections = sections
            card.overall = _overall(sections, card_weights)
            card.grade = _grade(card.overall)
            card.passed = card.overall >= card.threshold and not card.error
            card.suggestions = _overall_suggestions(sections)
        except Exception as exc:  # noqa: BLE001
            card.error = f"report card failed: {str(exc)[:400]}"
            card.overall = 0.0
            card.grade = "F"
            card.passed = False
        card.finished_at = _now()
        card.card_md = render_card_md(card)
        _write_text(report_dir / f"{_safe_name(name)}.md", card.card_md)
        card.card_json = json.dumps(card.to_dict(), indent=2, ensure_ascii=False,
                                    sort_keys=True, default=str)
        _write_text(report_dir / f"{_safe_name(name)}.json", card.card_json)
        summary.cards.append(card)
    summary.finished_at = _now()
    summary.exit_code = 0 if all(card.passed for card in summary.cards) else 1
    _write_text(report_dir / "summary.md", render_summary_md(summary))
    _write_json(report_dir / "summary.json", summary.to_dict())
    return summary

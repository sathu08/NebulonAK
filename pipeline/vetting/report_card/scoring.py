"""Section and overall scoring for the report card."""
from __future__ import annotations

from .models import SECTIONS, Check, Section, _grade

def _section_score(checks: list[Check]) -> float | None:
    scored = [check.score for check in checks if check.score is not None]
    if not scored:
        return None
    return round(100.0 * sum(scored) / len(scored), 2)


def _section_suggestions(checks: list[Check], limit: int = 5) -> list[str]:
    out = []
    for check in checks:
        if check.status not in ("fail", "error"):
            continue
        text = check.suggestion or check.detail or f"{check.name}: improve this check."
        if text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _make_section(name: str, checks: list[Check]) -> Section:
    score = _section_score(checks)
    return Section(
        name=name, checks=checks, score=score, grade=_grade(score),
        suggestions=_section_suggestions(checks),
    )


def _overall(sections: dict[str, Section],
             weights: dict[str, float]) -> float:
    total = 0.0
    total_weight = 0.0
    for name in SECTIONS:
        section = sections.get(name)
        if section is None or section.score is None:
            continue
        weight = float(weights.get(name, 0.0) or 0.0)
        if weight <= 0:
            continue
        total += section.score * weight
        total_weight += weight
    if total_weight <= 0:
        return 0.0
    return round(total / total_weight, 2)


def _overall_suggestions(sections: dict[str, Section],
                         limit: int = 5) -> list[str]:
    out = []
    for name in SECTIONS:
        section = sections.get(name)
        if section is None:
            continue
        for suggestion in section.suggestions:
            text = f"{name}: {suggestion}"
            if text not in out:
                out.append(text)
            if len(out) >= limit:
                return out
    return out


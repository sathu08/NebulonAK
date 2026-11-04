"""Markdown/JSON rendering and file helpers for the report card."""
from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from .models import SECTIONS, ReportCard, ReportSummary

def _clip(text: Any, limit: int = 1200) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "\n…(truncated)"


def _score_text(score: float | None) -> str:
    return "n/a" if score is None else f"{score:.1f}"


def render_card_md(card: ReportCard) -> str:
    lines = [
        f"# Report Card — {card.worker}",
        "",
        f"- Overall: **{card.overall:.1f}/100** ({card.grade}) — "
        f"{'PASS' if card.passed else 'FAIL'}",
        f"- Threshold: {card.threshold:.1f}/100",
        f"- Session: `{card.session_id}`",
        f"- Started: {card.started_at}",
        f"- Finished: {card.finished_at}",
        "",
        "## Scores",
        "",
        "| Section | Score | Grade | Checks |",
        "| --- | ---: | :---: | ---: |",
    ]
    for name in SECTIONS:
        section = card.sections.get(name)
        if section is None:
            continue
        scored = sum(1 for check in section.checks if check.score is not None)
        lines.append(
            f"| {name.title()} | {_score_text(section.score)}/100 | "
            f"{section.grade} | {scored} |")
    lines.append("")
    if card.error:
        lines.extend([f"> **Error:** {card.error}", ""])
    for name in SECTIONS:
        section = card.sections.get(name)
        if section is None:
            continue
        lines.extend([f"## {name.title()}", ""])
        for check in section.checks:
            lines.extend([
                f"### {check.name} — {_score_text(check.score)}",
                "",
                f"**Question:** {check.question}",
                "",
                f"**Expected:** {check.expected}",
                "",
                f"**Status:** {check.status}",
                "",
            ])
            if check.actual:
                lines.extend([f"**Answer:** {_clip(check.actual)}", ""])
            if check.detail:
                lines.extend([f"**Evidence:** {_clip(check.detail)}", ""])
            if check.suggestion:
                lines.extend([f"**Improve:** {check.suggestion}", ""])
        if section.suggestions:
            lines.extend(["### Where to improve", ""])
            lines.extend(f"- {suggestion}" for suggestion in section.suggestions)
            lines.append("")
    lines.extend(["## Overall improvement plan", ""])
    suggestions = card.suggestions or ["No major issues found."]
    lines.extend(f"- {suggestion}" for suggestion in suggestions)
    lines.append("")
    return "\n".join(lines)


def render_summary_md(summary: ReportSummary) -> str:
    lines = [
        "# Report Card Summary",
        "",
        f"- Run: `{summary.run_dir}`",
        f"- Report: `{summary.report_dir}`",
        f"- Started: {summary.started_at}",
        f"- Finished: {summary.finished_at}",
        "",
        "| Worker | Persona | Behavior | Tools | Craft | Overall | Grade | Verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | :---: | :---: |",
    ]
    for card in summary.cards:
        values = []
        for name in SECTIONS:
            section = card.sections.get(name)
            values.append(_score_text(section.score) if section else "n/a")
        lines.append(
            f"| {card.worker} | " + " | ".join(values)
            + f" | {card.overall:.1f} | {card.grade} | "
            f"{'PASS' if card.passed else 'FAIL'} |")
    if summary.skipped:
        lines.extend(["", "## Skipped", ""])
        lines.extend(f"- {item}" for item in summary.skipped)
    if summary.error:
        lines.extend(["", f"> **Error:** {summary.error}"])
    lines.append("")
    return "\n".join(lines)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, indent=2, ensure_ascii=False,
                                sort_keys=True, default=str))


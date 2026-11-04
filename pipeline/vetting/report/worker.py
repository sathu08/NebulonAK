"""Markdown/JSON reports for a single worker."""

from __future__ import annotations

import json

from pathlib import Path

from ..utils.models import WorkerResult
from .common import clip, format_score, probe_rows, write


def render_worker_md(result: WorkerResult) -> str:
    answers = {a.case_id: a for a in result.answers}
    judgements = {j.case_id: j for j in result.judgements}
    verdict = ("INCONCLUSIVE" if result.status == "inconclusive"
               else "PASS" if result.passed else "FAIL")
    score = ("n/a" if result.status == "inconclusive"
             else format_score(result.score))
    lines = [
        f"# Vetting report — {result.name}",
        "",
        f"- Role: `{result.profile.get('role', '')}`",
        f"- Score: **{score}** (threshold {format_score(result.threshold)})",
        f"- Verdict: **{verdict}**",
        f"- Status: `{result.status or 'unknown'}`",
        f"- Cases: {len(result.cases)}",
        f"- Inconclusive cases: {result.structural.get('inconclusive_cases', 0)}",
        f"- Session: `{result.session_id}`",
        f"- Started: {result.started_at}",
        f"- Finished: {result.finished_at}",
        "",
        "## Structural evidence",
        "",
        f"- Case score: {format_score(result.structural.get('case_score'))}",
        f"- Craft score: {format_score(result.structural.get('craft_score'))}",
        f"- Tool score: {format_score(result.structural.get('tool_score'))}",
        f"- Judge errors: {result.structural.get('judge_errors', 0)}",
        f"- Final score: {format_score(result.structural.get('final'))}",
        "",
        "## Cases",
        "",
    ]
    if result.error:
        lines.extend([f"> **Run error:** {result.error}", ""])
    for case in result.cases:
        answer = answers.get(case.id)
        judgement = judgements.get(case.id)
        lines.extend([
            f"### {case.id} — {case.difficulty} (weight {case.weight:g})",
            "",
            f"**Question:** {case.question}",
            "",
            f"**Expected:** {case.expected}",
            "",
            f"**Rubric:** {case.rubric}",
            "",
            f"**Score:** {format_score(judgement.score if judgement else 0.0)}",
            "",
        ])
        if answer:
            if answer.inconclusive:
                lines.extend([f"**Inconclusive:** "
                              f"{answer.inconclusive_reason or 'not scored'}",
                              ""])
            if answer.error:
                lines.extend([f"**Answer error:** {answer.error}", ""])
            if answer.policy_action:
                lines.extend([f"**Policy action:** `{answer.policy_action}`"
                              f"{' (blocked)' if answer.blocked else ''}", ""])
            lines.extend(["**Answer:**", "", "```text",
                          clip(answer.text), "```", ""])
        if judgement:
            if judgement.error:
                lines.extend([f"**Judge error:** {judgement.error}", ""])
            lines.extend([f"**Rationale:** {judgement.rationale or '(none)'}", ""])
            if judgement.evidence:
                lines.extend([f"**Evidence:** {clip(judgement.evidence, 800)}", ""])
        checks = [c for c in result.checks if c.get("case_id") == case.id]
        if checks:
            lines.extend(["**Deterministic checks:**", ""])
            for check in checks:
                mark = "pass" if check.get("passed") else "fail"
                lines.append(
                    f"- `{check.get('id', '')}` {check.get('op', '')}: "
                    f"**{mark}** (expected `{check.get('expected')!r}`)")
            lines.append("")
    lines.extend(["## Crafts", ""] + probe_rows(result.craft_probes) + [""])
    lines.extend(["## Tools", ""] + probe_rows(result.tool_probes) + [""])
    lines.extend(["## Where to improve", ""])
    if not result.improvements:
        lines.extend(["- (no failures to act on)", ""])
    for item in result.improvements:
        scope = f" [{item.scope}]" if item.scope else ""
        target = f" `{item.target}`" if item.target else ""
        lines.extend([
            f"- **{item.id}** ({item.severity}{scope}){target}: {item.problem}",
            f"  - Evidence: {clip(item.evidence, 500)}",
            f"  - Suggestion: {item.suggestion}",
        ])
        if item.affected_workers:
            lines.append(f"  - Affected workers: "
                         f"{', '.join(f'`{w}`' for w in item.affected_workers)}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_worker_report(result: WorkerResult, reports_dir: Path) -> None:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    slug = result.name
    result.report_md = str(reports_dir / f"{slug}.md")
    result.report_json = str(reports_dir / f"{slug}.json")
    result.report_md = write(reports_dir, f"{slug}.md", "md",
                             render_worker_md(result))
    result.report_json = write(
        reports_dir, f"{slug}.json", "json",
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )

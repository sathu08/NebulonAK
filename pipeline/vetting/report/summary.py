"""Markdown/JSON reports for a whole vetting run."""

from __future__ import annotations

import json

from pathlib import Path

from ..utils.models import RunSummary
from .common import clip, format_score, write


def render_summary_md(summary: RunSummary) -> str:
    lines = [
        "# Vetting run summary",
        "",
        f"- Run dir: `{summary.run_dir}`",
        f"- Started: {summary.started_at}",
        f"- Finished: {summary.finished_at}",
        f"- Cases per worker: {summary.cases}",
        f"- Threshold: {format_score(summary.threshold)}",
        f"- Exit code: {summary.exit_code}",
        "",
        "| Worker | Score | Verdict | Cases | Crafts | Tools | Error |",
        "| --- | ---: | --- | ---: | --- | --- | --- |",
    ]
    for w in summary.workers:
        crafts = f"{sum(1 for p in w.craft_probes if p.status == 'pass')}/" \
                 f"{sum(1 for p in w.craft_probes if p.status in ('pass', 'fail', 'error'))}"
        tools = f"{sum(1 for p in w.tool_probes if p.status == 'pass')}/" \
                f"{sum(1 for p in w.tool_probes if p.status in ('pass', 'fail', 'error'))}"
        verdict = ("INCONCLUSIVE" if w.status == "inconclusive"
                   else "PASS" if w.passed else "FAIL")
        score = ("n/a" if w.status == "inconclusive"
                 else format_score(w.score))
        lines.append(
            f"| {w.name} | {score} | {verdict} | "
            f"{len(w.cases)} | {crafts} | {tools} | {clip(w.error, 120) or ''} |"
        )
    if summary.skipped:
        lines.extend(["", "## Skipped", ""] + [f"- {s}" for s in summary.skipped])
    if summary.error:
        lines.extend(["", f"> **Run error:** {summary.error}"])
    lines.append("")
    return "\n".join(lines)


def write_summary(summary: RunSummary) -> None:
    root = Path(summary.run_dir)
    root.mkdir(parents=True, exist_ok=True)
    write(root, "summary.md", "md", render_summary_md(summary))
    write(root, "summary.json", "json",
          json.dumps(summary.to_dict(), indent=2, ensure_ascii=False,
                     sort_keys=True) + "\n")

"""Aggregate reports across repeated vetting runs (graded mode)."""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any


def render_aggregate_md(summary: Any) -> str:
    """Markdown table: worker medians, pass counts and graded verdicts."""
    lines = ["# Vetting aggregate summary", ""]
    lines.append(f"- Run dir: `{summary.run_dir}`")
    lines.append(f"- Started: {summary.started_at}")
    lines.append(f"- Finished: {summary.finished_at}")
    lines.append(f"- Repeats: {summary.repeats}")
    lines.append(f"- Cases from: `{summary.cases_from or 'generated'}`")
    lines.append(f"- Threshold: {summary.threshold:.3f}")
    lines.append(f"- Exit code: {summary.exit_code}")
    lines.append("")
    lines.append("| Worker | Median | Low | Passes | Verdict | Runs |")
    lines.append("| --- | ---: | ---: | ---: | --- | --- |")
    for worker in summary.workers:
        lines.append(
            f"| {worker.name} | {worker.median:.3f} | {worker.lowest:.3f} | "
            f"{worker.passes}/{worker.repeats} | {worker.verdict} | "
            + ", ".join(f"`{Path(p).name}`" for p in getattr(worker, "run_dirs", [])
                        ) or "`-`")
    if summary.error:
        lines.append("")
        lines.append(f"error: {summary.error}")
    lines.append("")
    return "\n".join(lines)


def write_aggregate(summary: Any, run_dir: str | Path) -> dict[str, str]:
    """Write aggregate.json + aggregate.md; return their paths."""
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = summary.to_dict()
    json_path = root / "aggregate.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                                    sort_keys=True),
                         encoding="utf-8")
    md_path = root / "aggregate.md"
    md_path.write_text(render_aggregate_md(summary), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}

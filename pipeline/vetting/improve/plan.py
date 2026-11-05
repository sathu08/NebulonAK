"""Collect findings and build a staged-craft plan."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nak_host.utils.settings import load_settings

from .constants import IMPROVE_DIR
from .paths import craft_source, load_summary, resolve_workspace, slug


def collect_improvements(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten per-worker improvements, de-duplicating identical findings."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for worker in summary.get("workers") or []:
        if not isinstance(worker, dict):
            continue
        for item in worker.get("improvements") or []:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("worker", worker.get("name", ""))),
                str(item.get("source", "")),
                str(item.get("target", "")),
                str(item.get("suggestion", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(item))
    return out


def plan_improvements(run_dir: str | Path, *,
                      crafts_prompt: str | Path | None = None,
                      workspace_root: str | Path | None = None) -> dict[str, Any]:
    """Return the staged-craft plan for a run without writing anything."""
    summary = load_summary(run_dir)
    root = resolve_workspace(workspace_root)
    prompt_root = (Path(crafts_prompt).expanduser() if crafts_prompt
                   else Path(load_settings().crafts_prompt_path()))
    run_id = Path(summary.get("run_dir") or run_dir).name
    improvements = collect_improvements(summary)
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in improvements:
        if str(item.get("scope", "")) != "craft":
            continue
        craft = str(item.get("target", "")).strip()
        if not craft or craft in seen:
            continue
        seen.add(craft)
        source = craft_source(craft, prompt_root)
        actions.append({
            "id": f"{run_id}-{slug(craft)}",
            "kind": "craft",
            "craft": craft,
            "source": str(source),
            "source_exists": source.is_file(),
            "staged": str(root / IMPROVE_DIR / run_id / "candidate"
                          / "craft" / craft / "craft.md"),
            "status": "planned",
            "affected_workers": list(item.get("affected_workers") or []),
            "improvement_ids": [str(item.get("id", ""))],
            "problem": str(item.get("problem", "")),
            "suggestion": str(item.get("suggestion", "")),
        })
    return {
        "run_dir": str(Path(run_dir)),
        "run_id": run_id,
        "workspace_root": str(root),
        "crafts_prompt": str(prompt_root),
        "improvements": improvements,
        "actions": actions,
        "push_status": "pending",
    }

"""Stage candidates, exercise them, verify with Nunnan and record history."""

from __future__ import annotations

import json
import shutil

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import HISTORY_FILE, IMPROVE_DIR
from .paths import resolve_workspace, slug
from .plan import plan_improvements
from .render import candidate_md
from .runtime import default_core_runner, default_verify


def append_history(workspace_root: Path, record: dict[str, Any]) -> Path:
    root = workspace_root / IMPROVE_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / HISTORY_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        history = raw if isinstance(raw, list) else []
    except (OSError, ValueError):
        history = []
    history.append(record)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def apply_improvements(
    run_dir: str | Path,
    *,
    workers: list[str] | None = None,
    workers_file: Any = None,
    crafts_prompt: str | Path | None = None,
    workspace_root: str | Path | None = None,
    threshold: float = 0.6,
    cases: int = 6,
    endpoint: str | None = None,
    timeout: float | None = None,
    test_core: bool = True,
    verify: bool = True,
    core_runner: Callable[..., dict[str, Any]] | None = None,
    verify_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stage craft candidates, test with ``core``, then re-vet with Nunnan."""
    plan = plan_improvements(run_dir, crafts_prompt=crafts_prompt,
                             workspace_root=workspace_root)
    root = Path(plan["workspace_root"])
    run_id = plan["run_id"]
    base = root / IMPROVE_DIR / run_id
    candidate_root = base / "candidate" / "craft"
    base.mkdir(parents=True, exist_ok=True)

    source_root = Path(plan["crafts_prompt"])
    if plan["actions"] and source_root.is_dir():
        if candidate_root.exists():
            shutil.rmtree(candidate_root)
        shutil.copytree(
            source_root,
            candidate_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.pyc"),
        )

    affected: list[str] = []
    for action in plan["actions"]:
        if not action["source_exists"]:
            action["status"] = "missing_source"
            continue
        target = candidate_root / action["craft"]
        target.mkdir(parents=True, exist_ok=True)
        original = Path(action["source"]).read_text(encoding="utf-8")
        improvement = next(
            (i for i in plan["improvements"]
             if str(i.get("target", "")) == action["craft"]), {})
        (target / "craft.md").write_text(
            candidate_md(original, improvement), encoding="utf-8")
        action["staged"] = str(target / "craft.md")
        action["status"] = "staged"
        for name in action.get("affected_workers") or []:
            if name not in affected:
                affected.append(name)

    (base / "plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    proposal = ["# Improvement proposal", "",
                f"- Run: `{plan['run_dir']}`", "",
                "## Findings", ""]
    for item in plan["improvements"]:
        proposal.append(
            f"- **{item.get('id', '')}** [{item.get('scope', '')}] "
            f"`{item.get('target', '')}`: {item.get('problem', '')}")
    proposal.extend(["", "## Staged actions", ""])
    for action in plan["actions"]:
        proposal.append(
            f"- `{action['craft']}`: **{action['status']}** "
            f"-> `{action['staged']}`")
    proposal.extend(["", "Push status: **pending** (manual review required).", ""])
    (base / "proposal.md").write_text("\n".join(proposal), encoding="utf-8")

    selected = workers or affected
    record: dict[str, Any] = {
        "run_id": run_id,
        "run_dir": plan["run_dir"],
        "workspace_root": str(root),
        "candidate_root": str(candidate_root),
        "improvement_ids": [str(i.get("id", ""))
                            for i in plan["improvements"]],
        "actions": plan["actions"],
        "affected_workers": affected,
        "verified_workers": selected,
        "status": "staged",
        "push_status": "pending",
    }

    staged = [a for a in plan["actions"] if a["status"] == "staged"]
    if test_core and staged:
        core = core_runner or default_core_runner
        results = []
        for action in staged:
            goal = (
                f"Return a numbered plan that smoke-tests the staged "
                f"'{action['craft']}' craft inside the workspace: write a "
                f"short note file, read it back, and report the result. "
                f"Address this finding: {action['problem']} "
                f"Required change: {action['suggestion']}"
            )
            try:
                result = core(goal, action["craft"], candidate_root,
                              workers_file=workers_file,
                              workspace_root=root)
            except Exception as exc:  # noqa: BLE001 — record, never raise
                result = {"ok": False, "error": str(exc)[:400]}
            results.append({"craft": action["craft"], **result})
            action["status"] = "tested" if result.get("ok") else "test_failed"
        record["core"] = results
        record["status"] = ("tested" if all(r.get("ok") for r in results)
                            else "test_failed")

    if (verify and staged and record["status"] != "test_failed"
            and selected):
        verify_call = verify_fn or default_verify
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            result = verify_call(
                candidate_root, selected,
                workers_file=workers_file,
                run_dir=base / "verify" / stamp,
                threshold=threshold,
                cases=cases,
                endpoint=endpoint,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 — record, never raise
            result = {"ok": False, "error": str(exc)[:400]}
        record["verify"] = result
        record["status"] = "verified" if result.get("ok") else "verify_failed"

    record["history"] = str(append_history(root, record))
    (base / "result.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return record


def discard_candidate(workspace_root: str | Path | None, run_id: str) -> bool:
    """Remove a staged candidate tree (history is kept)."""
    base = resolve_workspace(workspace_root) / IMPROVE_DIR / slug(run_id)
    if not base.is_dir():
        return False
    shutil.rmtree(base)
    return True

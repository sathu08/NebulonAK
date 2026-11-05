"""Default runners used to exercise and verify staged candidates."""

from __future__ import annotations

import contextlib
import os

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..utils.runner import run_vetting


@contextlib.contextmanager
def craft_root_env(root: str | Path) -> Iterator[None]:
    old = os.environ.get("NAK_CRAFTS_PROMPT")
    os.environ["NAK_CRAFTS_PROMPT"] = str(root)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("NAK_CRAFTS_PROMPT", None)
        else:
            os.environ["NAK_CRAFTS_PROMPT"] = old


def default_core_runner(goal: str, craft: str, staged_root: Path, *,
                        workers_file: Any, workspace_root: Path) -> dict[str, Any]:
    from nak_host.wiring import make_harness, run_craft

    with craft_root_env(staged_root):
        harness = make_harness(workspace_root=str(workspace_root),
                               workers_file=workers_file)
        result = run_craft(goal, harness=harness, team="core",
                           approver=lambda info: True)
    error = str(result.get("error") or result.get("reason") or "")
    return {
        "ok": bool(result.get("planned") and not error),
        "craft": craft,
        "planned": bool(result.get("planned")),
        "answer": str(result.get("answer", "") or "")[:4000],
        "error": error,
    }


def default_verify(staged_root: Path, workers: list[str], *,
                   workers_file: Any, run_dir: Path,
                   threshold: float, cases: int,
                   endpoint: str | None, timeout: float | None) -> dict[str, Any]:
    with craft_root_env(staged_root):
        summary = run_vetting(
            workers,
            run_dir=run_dir,
            workers_file=workers_file,
            endpoint=endpoint,
            timeout=timeout,
            cases=cases,
            threshold=threshold,
        )
    return {
        "ok": summary.exit_code == 0,
        "exit_code": summary.exit_code,
        "run_dir": summary.run_dir,
        "workers": [
            {"name": w.name, "status": w.status, "score": w.score,
             "passed": w.passed}
            for w in summary.workers
        ],
    }

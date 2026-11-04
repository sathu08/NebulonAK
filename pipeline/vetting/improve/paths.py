"""Filesystem helpers for locating runs, workspaces and craft sources."""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from nak.utils.settings import load_settings


def load_summary(run_dir: str | Path) -> dict[str, Any]:
    path = Path(run_dir) / "summary.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read vetting summary {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"summary {path} is not a JSON object")
    return raw


def resolve_workspace(workspace_root: str | Path | None) -> Path:
    if workspace_root:
        return Path(workspace_root).expanduser()
    settings = load_settings()
    configured = settings.workspace_path()
    if configured:
        return Path(configured).expanduser()
    from nak.harness.engine.sandbox import default_workspace

    return Path(default_workspace()).expanduser()


def slug(value: Any) -> str:
    text = "".join(ch if ch.isalnum() or ch in "-_." else "_"
                   for ch in str(value or "").strip())
    return text.strip("._") or "item"


def craft_source(craft: str, crafts_prompt: str | Path) -> Path:
    return Path(crafts_prompt).expanduser() / str(craft) / "craft.md"

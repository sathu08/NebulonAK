"""Disk store — the ONLY place that touches the pipeline directory.

Layout:
  pipeline/vetting/            builtin code, read-only here
  <pipeline_dir>/<slug>/       user pipelines from the Builder (configurable via
                               [pipeline] directory in nebulonak.cfg, default pipeline/UI)
    pipeline.json              canonical definition
    README.md                  auto-generated
    runs/<stamp>/result.json   execution output (gitignored)
  pipeline/index.json          registry [{slug,name,updated_at}]
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SLUG_RX = re.compile(r"^[a-z0-9-]{1,64}$")
RESERVED = {"vetting"}


def pipeline_root() -> Path:
    from nak_host.utils.settings import load_settings

    try:
        return Path(load_settings().home_path()) / "pipeline"
    except Exception:
        return Path(__file__).resolve().parents[2] / "pipeline"


def custom_root() -> Path:
    """Configurable user-pipeline location ([pipeline] directory, default pipeline/UI)."""
    try:
        from nak_host.utils.settings import load_settings

        return Path(load_settings().pipeline_dir_path())
    except Exception:
        return pipeline_root() / "custom"


def slugify_name(name: str) -> str:
    """Auto slug from pipeline name (UI no longer asks for slug)."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    return s[:64] or "pipeline"


def index_path() -> Path:
    return pipeline_root() / "index.json"


def _slug_error(slug: str) -> str | None:
    s = str(slug or "").strip().lower()
    if not s or s in RESERVED:
        return f"slug {s!r} is reserved (builtin)" if s else "slug must be non-empty"
    if not _SLUG_RX.match(s) or ".." in s or "/" in s or "\\" in s:
        return "slug must match ^[a-z0-9-]{1,64}$"
    return None


def builtin_vetting_template() -> dict[str, Any]:
    """The 'vetting' pipeline template (prompt -> tool -> craft)."""
    return {
        "name": "vetting",
        "slug": "vetting",
        "description": "Built-in vetting template: prompt -> tool -> craft (single chain).",
        "worker": "viyan",
        "team": "",
        "nodes": [
            {
                "id": "n1",
                "type": "prompt",
                "label": "Ask question",
                "x": 60,
                "y": 140,
                "config": {"text": "Grade worker viyan"},
            },
            {
                "id": "n2",
                "type": "tool",
                "label": "Read file",
                "x": 340,
                "y": 140,
                "config": {"tool": "file", "args": {"action": "read", "path": "notes.md"}},
            },
            {
                "id": "n3",
                "type": "craft",
                "label": "Plan craft",
                "x": 620,
                "y": 140,
                "config": {"craft": "plan", "goal": "ship login"},
            },
        ],
        "edges": [{"from_id": "n1", "to_id": "n2"}, {"from_id": "n2", "to_id": "n3"}],
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return None


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def list_definitions() -> list[dict[str, Any]]:
    """All pipelines: builtin 'vetting' first, then pipeline/UI/*."""
    out: list[dict[str, Any]] = [
        {
            "slug": "vetting",
            "name": "vetting",
            "builtin": True,
            "description": "Built-in template (prompt->tool->craft)",
        }
    ]
    root = custom_root()
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            if not _SLUG_RX.match(child.name):
                continue
            if not (child / "pipeline.json").is_file():
                continue  # run-history orphan, not a pipeline
            meta = _read_json(child / "pipeline.json") or {}
            out.append(
                {
                    "slug": child.name,
                    "name": str(meta.get("name", "") or child.name),
                    "builtin": False,
                    "description": str(meta.get("description", "") or ""),
                    "nodes": len(meta.get("nodes") or []) if isinstance(meta.get("nodes"), list) else 0,
                }
            )
    return out


def get_definition(slug: str) -> dict[str, Any] | None:
    s = str(slug or "").strip().lower()
    if s == "vetting":
        return builtin_vetting_template()
    if _slug_error(s):
        return None
    return _read_json(custom_root() / s / "pipeline.json")


def list_runs(slug: str, limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first run summaries for one pipeline.

    Reads ``<pipeline_dir>/<slug>/runs/<stamp>/result.json``. The builtin
    ``vetting`` template never persists runs, so it always yields ``[]``.
    """
    s = str(slug or "").strip().lower()
    if not s or s in RESERVED:
        return []
    runs_dir = custom_root() / s / "runs"
    out: list[dict[str, Any]] = []
    if runs_dir.is_dir():
        for stamp in sorted((d for d in runs_dir.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True):
            payload = _read_json(stamp / "result.json")
            if payload is None:
                continue
            out.append(
                {
                    "stamp": stamp.name,
                    "ok": bool(payload.get("ok", False)),
                    "worker": str(payload.get("worker", "") or ""),
                    "path": str(stamp / "result.json"),
                    "summary": str(payload.get("summary", "") or "")[:200],
                }
            )
            if len(out) >= max(1, min(100, int(limit or 20))):
                break
    return out


def save_definition(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + persist to <pipeline_dir>/<slug>/ (slug auto from name).

    Returns the saved dict (with slug normalized). Raises ValueError.
    """
    from nak_host.flow.engine.chain import validate_definition

    if not isinstance(raw, dict):
        raise ValueError("definition must be an object")
    # Slug is internal only (UI hides it): auto from name, or keep explicit if valid.
    given = str(raw.get("slug") or "").strip().lower()
    slug = given if (given and given != "vetting" and _SLUG_RX.match(given)) else slugify_name(raw.get("name"))
    if slug in RESERVED:
        slug = slug + "-copy"
    err = _slug_error(slug)
    if err:
        raise ValueError(err)
    name = str(raw.get("name") or slug).strip()[:120] or slug
    check = validate_definition({**raw, "slug": slug, "name": name})
    if not check["ok"]:
        raise ValueError("; ".join(check["errors"][:5]))
    data = {
        "name": name,
        "slug": slug,
        "description": str(raw.get("description", "") or "")[:500],
        "worker": str(raw.get("worker", "") or "viyan").strip()[:64] or "viyan",
        "team": str(raw.get("team", "") or "").strip()[:64],
        "nodes": raw.get("nodes"),
        "edges": raw.get("edges"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    dest = custom_root() / slug
    _write_json_atomic(dest / "pipeline.json", data)
    readme = dest / "README.md"
    if not readme.is_file():
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(f"# {name}\n\nCreated in Builder. Edit in Builder, runs go to `runs/`.\n", encoding="utf-8")
    _refresh_index()
    return data


def delete_definition(slug: str) -> bool:
    import shutil

    s = str(slug or "").strip().lower()
    if _slug_error(s):
        raise ValueError(f"cannot delete {slug!r}")
    target = custom_root() / s
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    _refresh_index()
    return True


def _refresh_index() -> None:
    try:
        items = []
        for entry in list_definitions():
            if entry.get("builtin"):
                continue
            items.append(
                {
                    "slug": entry["slug"],
                    "name": entry.get("name", ""),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        _write_json_atomic(index_path(), {"pipelines": items})
    except OSError:
        pass

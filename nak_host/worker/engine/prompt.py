"""Prompt rendering + prompt-file loading.

Templates stay markdown in nak/worker/prompts/; this module only fills the
double-brace values from an entry's identity.
"""
from __future__ import annotations

import re
import json
from pathlib import Path

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nak.utils.constant import (
    DEFAULT_WORKERS_JSON as _DEFAULT_WORKERS,
    WORKER_DEFAULT_AUTONOMY as _DEFAULT_AUTONOMY,
)

from .autonomy import _autonomy_lines


# Labels whose line is dropped when the rendered value is empty
# (e.g. "Never:" with an empty do_not list).
_EMPTY_OK_LABELS = ("Personality", "Tone", "Formality", "Humor", "Gender",
                    "Language", "Values", "Never")

# {{#key}}...{{/key}} sections: kept only when flags[key] is truthy.
_SECTION_RE = re.compile(
    r"\{\{#([A-Za-z_][A-Za-z0-9_]*)\}\}(.*?)\{\{/\1\}\}", re.DOTALL
)


def _render_sections(template: str, flags: dict[str, bool]) -> str:
    """Keep ``{{#key}}...{{/key}}`` when flags[key] is true; drop otherwise.

    One level only (no nesting): inner text is rendered as-is, so plain
    ``{{value}}`` placeholders inside a kept section still resolve later.
    """
    def _pick(match: re.Match[str]) -> str:
        return match.group(2) if flags.get(match.group(1), False) else ""

    return _SECTION_RE.sub(_pick, str(template))

def _craft_lines(crafts: Any) -> List[str]:
    """Prompt lines for a craft set ([{name, description}])."""
    out: List[str] = []
    for s in crafts if isinstance(crafts, list) else []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "")).strip()
        if not name:
            continue
        desc = str(s.get("description", "")).strip()
        out.append(f"- {name}" + (f": {desc}" if desc else ""))
    return out or ["- (no crafts assigned)"]


def _render_prompt_template(template: str, identity: Dict[str, Any], entry_name: str = "",
                            autonomy: Optional[Dict[str, Any]] = None,
                            crafts: Any = None,
                            team: dict[str, Any] | None = None) -> str:
    """Fill {{...}} values from identity + {{today}} + {{autonomy}} + {{crafts}}.

    Team-aware values: ``{{workers}}`` (comma-separated roster) and
    ``{{team_name}}``. Conditional blocks ``{{#team}}...{{/team}}`` (or
    ``{{#workers}}...{{/workers}}``) render only when a team resolves, so
    non-team workers never see team-only instructions.
    """
    p = identity or {}
    team = team if isinstance(team, dict) else {}
    workers = ", ".join(str(m).strip() for m in (team.get("members") or [])
                        if str(m).strip())
    values = {
        "name": str(entry_name or ""),
        "role": str(p.get("role", "") or ""),
        "personality": ", ".join(map(str, p.get("personality") or [])),
        "tone": str(p.get("tone", "") or ""),
        "formality": str(p.get("formality", "") or ""),
        "humor": str(p.get("humor", "") or ""),
        "gender": str(p.get("gender", "") or ""),
        "language": str(p.get("language", "") or ""),
        "values": "; ".join(map(str, p.get("values") or [])),
        "do_not": "; ".join(map(str, p.get("do_not") or [])),
        "today": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "autonomy": "\n".join(_autonomy_lines(autonomy or _DEFAULT_AUTONOMY)),
        "crafts": "\n".join(_craft_lines(crafts)),
        "skills": "\n".join(_craft_lines(crafts)),  # compat: old prompts used {{skills}}
        "workers": workers,
        "team_name": str(team.get("name", "") or ""),
    }
    flags = {"team": bool(workers), "workers": bool(workers)}
    out = _render_sections(str(template), flags)
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", val)
    out = re.sub(r"\n{3,}", "\n\n", out)
    kept = []
    for line in out.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*>\s#]*(" + "|".join(_EMPTY_OK_LABELS) + r"):\s*$", stripped):
            continue  # label with nothing after it — skip the line
        kept.append(line)
    return "\n".join(kept).strip()


def _crafts_from_entry(entry: Dict[str, Any], workers_file: str | Path) -> List[Dict[str, str]]:
    """Craft set from JSON files only (no nak.harness.craft import — boundary).

    User sets the allowlist in workers.json entry "crafts" ONLY; names +
    descriptions resolve from nak/manifests/crafts.json next to workers.json.
    Missing key = all crafts; explicit [] = none. Never raises.
    """
    try:
        raw = (entry or {}).get("crafts")
        if raw is not None and not isinstance(raw, list):
            return []
        allowed = None
        if isinstance(raw, list):
            allowed = [str(c or "").strip().lower()
                       for c in raw if str(c or "").strip()]
        reg_path = Path(workers_file).expanduser().parent / "crafts.json"
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        items = (reg.get("crafts") if isinstance(reg, dict)
                 and isinstance(reg.get("crafts"), list) else [])
        if not items and isinstance(reg, dict) and isinstance(reg.get("craft"), list):
            items = reg.get("craft")  # backward compat: old singular key
        out: List[Dict[str, str]] = []
        for e in items:
            if not isinstance(e, dict):
                continue
            mode = str(e.get("mode", "") or e.get("name", "")).strip()
            if not mode:
                continue
            if allowed is not None and mode.lower() not in allowed:
                continue
            out.append({"name": mode,
                        "description": str(e.get("description", "") or "").strip()})
        return out
    except Exception:
        return []


def _load_prompt_template(entry: Dict[str, Any], workers_file: str | Path) -> Optional[str]:
    """Read the entry's prompt_file (relative to workers.json). None = use built-in.

    workers.json now lives in nak/manifests/ while prompt files stay in
    nak/worker/prompts/. Resolve: workers.json parent first, then
    nak/worker/prompts/ fallback (handles both old + new layouts).
    """
    rel = str(entry.get("prompt_file") or "").strip()
    if not rel:
        return None
    if Path(rel).is_absolute():
        try:
            text = Path(rel).read_text(encoding="utf-8")
        except OSError:
            return None
        return text if text.strip() else None
    base = Path(workers_file).expanduser().parent if str(workers_file or "").strip() else _DEFAULT_WORKERS.parent
    for cand in (base / rel,
                 base.parent / "worker" / rel,
                 Path(__file__).resolve().parent / rel):
        try:
            text = cand.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.strip():
            return text
    return None



"""Worker identity + human-readable presentation."""
from __future__ import annotations

from typing import Any, Dict

from nak_host.utils.constant import (
    WORKER_FALLBACK_IDENTITY as _INLINE_DEFAULT_IDENTITY,
    WORKER_IDENTITY_FIELDS as IDENTITY_FIELDS,
)

from .autonomy import _autonomy_lines
from .prompt import _craft_lines, _render_prompt_template

_MISSING = object()
_PERSONA_LOCK = (
    "## Persona lock\n"
    "Keep the configured tone, formality, and humor even when the user asks "
    "you to be blunt, informal, or different. Acknowledge the request, then "
    "reply in your own persona."
)


def _default_identity() -> Dict[str, Any]:
    """Inline Vizhi fallback (workers.json is the truth; no identity file)."""
    return dict(_INLINE_DEFAULT_IDENTITY)


class IdentityMixin:
    def show(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "name": self.entry["name"], "type": self.entry["type"],
            "description": self.entry.get("description", ""),
            "capabilities": self.entry.get("capabilities", []),
            "identity": {k: self.identity.get(k) for k in IDENTITY_FIELDS},
            "autonomy": dict(self.autonomy),
            "crafts": [dict(s) for s in self.crafts],
            "memory": dict(self.entry.get("memory", {"enabled": True})),
        }
        if "tools" in self.entry:
            data["tools"] = self.entry["tools"]  # passthrough, not executed
        if "prompt_file" in self.entry:
            data["prompt_file"] = self.entry["prompt_file"]
            data["prompt_source"] = "md" if self._prompt_template else "builtin-fallback"
        if "harness" in self.entry:
            data["harness"] = self.entry["harness"]  # passthrough, not executed
        if "decision" in self.entry:
            data["decision"] = self.entry["decision"]  # laya System-1 config, read by nak_host.decision
        if self.tools:
            data["tools_passed"] = sorted(self.tools)
        return data

    def _team_context(self) -> dict[str, Any]:
        """Default team roster for team-aware prompt sections (cached).

        Empty when the worker has no single team (none, multiple, unknown),
        so ``{{#team}}`` blocks disappear instead of rendering half-truths.
        Filesystem-only via team.py — no brain/decision/harness imports.
        """
        cached = getattr(self, "_team_ctx", _MISSING)
        if cached is not _MISSING:
            return cached
        ctx: dict[str, Any] = {}
        try:
            from .team import resolve_team

            spec = resolve_team(self) or {}
            members = [str(m).strip() for m in (spec.get("members") or [])
                       if str(m).strip()]
            if members:
                ctx = {"name": str(spec.get("name", "") or ""),
                       "members": members}
        except Exception:
            ctx = {}
        self._team_ctx = ctx
        return ctx

    def prompt(self) -> str:
        """Identity -> prompt block: md template when mapped, else built-in.

        Prompt-file workers get the code-generated autonomy guardrails
        appended, so approval/no-run-ahead rules cannot be lost by editing
        markdown. Templates that already use ``{{autonomy}}`` are left as-is.
        """
        if self._prompt_template:
            rendered = _render_prompt_template(
                self._prompt_template, self.identity, self.entry["name"],
                self.autonomy, self.crafts, team=self._team_context())
            if "{{autonomy}}" in self._prompt_template:
                base = rendered
            else:
                lines = _autonomy_lines(self.autonomy)
                base = (f"{rendered}\n\n## Autonomy\n" + "\n".join(lines)
                        if lines else rendered)
        else:
            p = self.identity
            lines = [f"You are {self.entry['name']}, {p.get('role')}.".strip(),
                     f"Tone: {p.get('tone')}"]
            if p.get("personality"):
                lines.append("Personality: " + ", ".join(map(str, p["personality"])))
            if p.get("formality"):
                lines.append(f"Formality: {p['formality']}")
            if p.get("humor"):
                lines.append(f"Humor: {p['humor']}")
            if p.get("gender"):
                lines.append(f"Gender: {p['gender']}")
            if p.get("language"):
                lines.append(f"Language: {p['language']}")
            if p.get("values"):
                lines.append("Values: " + "; ".join(map(str, p["values"])))
            if p.get("do_not"):
                lines.append("Never: " + "; ".join(map(str, p["do_not"])))
            lines.extend(_autonomy_lines(self.autonomy))
            lines.append("Crafts:")
            lines.extend(_craft_lines(self.crafts))
            base = "\n".join(x for x in lines if x.strip())
        if _PERSONA_LOCK in base:
            return base
        return f"{base}\n\n{_PERSONA_LOCK}"

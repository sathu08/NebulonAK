"""Crafts-derived autonomy + craft enforcement for one worker.

Pure helpers: read crafts.json/workers.json only. No brain/decision/harness.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from nak_host.utils.constant import (
    CRAFT_MAX_STEPS,
    CRAFT_MIN_STEPS,
    CRAFT_VALID_APPROVALS as _VALID_APPROVALS,
    DEFAULT_WORKERS_JSON as _DEFAULT_WORKERS,
    WORKER_DEFAULT_AUTONOMY as _DEFAULT_AUTONOMY,
    WORKER_FALLBACK_MODES as _FALLBACK_MODES,
    WORKER_FALLBACK_TYPES as _FALLBACK_TYPES,
)


def _worker_types(workers_file: str | Path | None = None) -> list[str]:
    """Distinct worker types declared in workers.json itself (file order).

    workers.json is its own truth for types: a brand-new type is valid
    the moment you write it on an entry — no Python edit needed. Use
    for listing/inspection (CLI, reports). Unreadable file -> fallback
    list. Never raises.
    """
    try:
        cand = Path(workers_file).expanduser() if str(workers_file or "").strip() \
            else _DEFAULT_WORKERS
        raw = json.loads(cand.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return list(_FALLBACK_TYPES)
    if not isinstance(raw, list):
        return list(_FALLBACK_TYPES)
    out: list[str] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        t = str(e.get("type", "") or "").strip()
        if t and t not in out:
            out.append(t)
    return out or list(_FALLBACK_TYPES)


def _registry_path(workers_file: str | Path | None = None) -> Path:
    """crafts.json path: NAK_CRAFTS env > next to workers.json > default."""
    env_path = str(os.environ.get("NAK_CRAFTS", "") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    if str(workers_file or "").strip():
        return Path(workers_file).expanduser().parent / "crafts.json"
    return _DEFAULT_WORKERS.parent / "crafts.json"


def _craft_modes(workers_file: str | Path | None = None) -> list[str]:
    """Craft modes from the crafts.json registry + implicit "assist".

    crafts.json is the truth for modes (a new pin is usable with no
    Python edit); "assist" is the implicit mode for workers with an
    explicit empty crafts list. Unreadable registry -> fallback list.
    Never raises.
    """
    try:
        reg_path = _registry_path(workers_file)
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        items = (reg.get("crafts") if isinstance(reg, dict)
                 and isinstance(reg.get("crafts"), list) else [])
        if not items and isinstance(reg, dict) and isinstance(reg.get("craft"), list):
            items = reg.get("craft")
    except (OSError, ValueError):
        return list(_FALLBACK_MODES)
    out: list[str] = []
    for e in (items or []):
        if not isinstance(e, dict):
            continue
        mode = str(e.get("mode", "") or e.get("name", "")).strip().lower()
        if mode and mode != "assist" and mode not in out:
            out.append(mode)
    return out or list(_FALLBACK_MODES)


def _autonomy_from_crafts(allowed: Any, workers_file: str | Path,
                          entry_name: str) -> dict[str, Any]:
    """Derive worker behavior from its crafts allowlist + crafts.json pins.

    crafts is the ONLY source of truth (workers.json carries no autonomy
    block). Rules:
      * allowed None (missing key) = all crafts in registry.
      * allowed [] (explicit empty) = no crafts -> assist (answer directly).
      * modes = registry-order craft modes filtered to the allowlist.
      * approval = most restrictive pin (all > small > none).
      * max_steps = max pin value (default 3) over the PLANNING crafts
        only (plan/build); other crafts never cap steps. A coder with
        plan+build gets the build budget, not the plan budget.
      * auto_steps = min pin value (default 2), capped to max_steps.
    Never raises on unreadable registry — falls back to plan/ask default.
    """
    try:
        reg_path = _registry_path(workers_file)
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        items = (reg.get("crafts") if isinstance(reg, dict)
                 and isinstance(reg.get("crafts"), list) else [])
        if not items and isinstance(reg, dict) and isinstance(reg.get("craft"), list):
            items = reg.get("craft")
    except (OSError, ValueError):
        return dict(_DEFAULT_AUTONOMY)
    pins: list[dict[str, Any]] = []
    valid_modes = set(_craft_modes(workers_file)) | {"assist"}
    for e in items:
        if not isinstance(e, dict):
            continue
        mode = str(e.get("mode", "") or e.get("name", "")).strip().lower()
        if mode not in valid_modes or mode == "assist":
            continue
        pins.append({"mode": mode,
                     "max_steps": e.get("max_steps", 3),
                     "approval": str(e.get("approval", "all") or "all").strip().lower(),
                     "auto_steps": e.get("auto_steps", 2)})
    if allowed is None:
        selected = pins
    elif isinstance(allowed, list):
        want = [str(c or "").strip().lower() for c in allowed if str(c or "").strip()]
        selected = [p for p in pins if p["mode"] in want]
    else:
        selected = []
    if not selected:
        return {"modes": ["assist"], "max_steps": 3,
                "approval": "all", "auto_steps": 2}
    modes = [p["mode"] for p in selected]
    # Step limits belong to planning crafts only (plan/build). A non-planning
    # craft (assign/assess/...) must never cap the plan step budget.
    planning = [p for p in selected if p["mode"] in ("plan", "build")]
    policy = planning or selected
    try:
        steps = max(max(CRAFT_MIN_STEPS, int(p.get("max_steps", 3))) for p in policy)
    except (TypeError, ValueError):
        steps = 3
    steps = max(CRAFT_MIN_STEPS, min(CRAFT_MAX_STEPS, steps))
    apprs = [p.get("approval", "all") for p in selected]
    approval = "all" if "all" in apprs else ("small" if "small" in apprs else "none")
    if approval not in _VALID_APPROVALS:
        approval = "all"
    try:
        auto_n = min(max(CRAFT_MIN_STEPS, int(p.get("auto_steps", 2))) for p in policy)
    except (TypeError, ValueError):
        auto_n = 2
    auto_n = max(CRAFT_MIN_STEPS, min(steps, auto_n))
    return {"modes": modes, "max_steps": steps,
            "approval": approval, "auto_steps": auto_n}


def _autonomy_lines(autonomy: dict[str, Any]) -> list[str]:
    """Prompt sentences composed in code from crafts-derived modes (never model-written)."""
    modes = (autonomy or {}).get("modes", ["assist"])
    steps = (autonomy or {}).get("max_steps", 3)
    if "assist" in modes:
        return ["Autonomy: assist — answer directly, do not propose plans "
                "or name other workers."]
    lines = [f"Autonomy: {', '.join(modes)} (max {steps} steps)."]
    if "plan" in modes or "build" in modes:
        lines.append(f"- You MAY propose a numbered plan (at most {steps} steps), "
                     "then STOP and wait for approval.")
        lines.append("- Answer questions directly; only propose a plan when the "
                     "user asks you to do work.")
    else:
        lines.append("- You may NOT propose plans — answer directly.")
    if "build" in modes:
        lines.append("- You MAY implement approved build steps via file writes "
                     "and terminal checks, one step at a time.")
    if "assign" in modes:
        lines.append("- You MAY tag steps with [worker=name] using only names "
                     "from the worker registry; unknown names are ignored.")
    else:
        lines.append("- You may NOT name other workers.")
    lines.append("- Do not run ahead — propose, then wait.")
    appr = (autonomy or {}).get("approval", "all")
    try:
        auto_n = int((autonomy or {}).get("auto_steps", 2))
    except (TypeError, ValueError):
        auto_n = 2
    if appr == "all":
        lines.append("- Approval: every plan waits for approval before running.")
    elif appr == "none":
        lines.append("- Approval: plans run without asking (harness write-gates still apply).")
    else:
        lines.append(f"- Approval: plans with {auto_n} or fewer steps run ahead; "
                     f"plans with more than {auto_n} steps (max {steps}) wait "
                     "for approval.")
    if "plan" in modes or "build" in modes:
        if appr == "all":
            lines.append(f"- Whenever you describe or propose a plan, state the "
                         f"step limit (max {steps}) and that it waits for "
                         "approval before running.")
        elif appr == "none":
            lines.append(f"- Whenever you describe or propose a plan, state the "
                         f"step limit (max {steps}) and that it runs without "
                         "asking.")
        else:
            lines.append(f"- Whenever you describe or propose a plan, state the "
                         f"step limit (max {steps}) and that plans with {auto_n} "
                         "or fewer steps run ahead while larger plans wait for "
                         "approval.")
    return lines


class AutonomyMixin:
    def can(self, mode: str) -> bool:
        """True when mode is in the crafts-derived modes (assist/plan/assign)."""
        return str(mode or "").strip().lower() in (self.autonomy.get("modes") or [])

    def needs_plan_approval(self, n_steps: Any) -> bool:
        """True when a plan of n_steps must wait for approval.

        approval 'all' -> always; 'none' -> never (harness write-gates
        still apply); 'small' -> only when n_steps > auto_steps.
        """
        try:
            n = int(n_steps)
        except (TypeError, ValueError):
            return True
        mode = str(self.autonomy.get("approval", "all") or "all").strip().lower()
        if mode == "none":
            return False
        if mode == "small":
            try:
                lim = int(self.autonomy.get("auto_steps", 2))
            except (TypeError, ValueError):
                lim = 2
            return n > lim
        return True

    @staticmethod
    def extract_plan(answer: str) -> list[str]:
        """Numbered steps out of a plan reply (post-truncation text)."""
        steps: list[str] = []
        for ln in str(answer or "").splitlines():
            m = re.match(r"^\s*(\d+)[.)]\s+(\S.*)$", ln)
            if m:
                steps.append(m.group(2).strip())
        return steps

    @staticmethod
    def extract_assigned(answer: str, known: list[str] | None = None) -> list[str]:
        """[worker=name] tags out of a plan reply (unknown names dropped)."""
        names = [m.group(1).strip()
                 for m in re.finditer(r"\[\s*worker\s*=\s*([^\]\s]+)\s*\]",
                                      str(answer or ""), re.IGNORECASE)]
        seen: list[str] = []
        for n in names:
            if n and n not in seen and (known is None or n in known):
                seen.append(n)
        return seen

    def _enforce_autonomy(self, answer: str) -> str:
        """Enforce crafts-derived modes on model output (in code, never by asking nicely).

        - no 'plan'/'build': drop 'Plan:' header lines, keep the rest as plain answer.
        - 'plan'/'build': truncate numbered steps beyond max_steps, append note.
        - no 'assign': strip [worker=name] tags (keep inner text).
        """
        text = str(answer or "")
        if not text.strip():
            return text
        if not self.can("plan") and not self.can("build"):
            kept = [ln for ln in text.splitlines()
                    if not re.match(r"^\s*plan\s*:", ln, re.IGNORECASE)]
            text = "\n".join(kept).strip() or text
        else:
            try:
                limit = int(self.autonomy.get("max_steps", 3))
            except (TypeError, ValueError):
                limit = 3
            lines = text.splitlines()
            step_idx = [i for i, ln in enumerate(lines)
                        if re.match(r"^\s*\d+[.)]\s+\S", ln)]
            if len(step_idx) > limit:
                cut = step_idx[limit]
                lines = lines[:cut] + [f"[plan truncated to {limit} steps]"]
                text = "\n".join(lines)
        if not self.can("assign"):
            text = re.sub(r"\[\s*worker\s*=\s*[^\]]+\]\s*", "", text,
                           flags=re.IGNORECASE)
        return text

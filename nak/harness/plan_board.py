"""nak.harness.plan_board -- shared md board for huge multitask runs.

All md lives under:
    workspace/session_<id>/artifacts/nak_plan.md   (master board, Polaris creates)
    workspace/session_<id>/artifacts/slice-<part>.md (per-agent scratch, optional)

Master columns:
| slice | tasks | agent | files | test | status | mind_key | updated |

Workers flip markers only via mark_slice() (never full overwrite) so up to
5 parallel agents don't clobber each other. Mind mirror happens via the
existing project_memory.store_turn() (1 line per slice, same mind_key).

Gating (huge-only): board is created/updated ONLY when multitask is enabled
in cfg AND the task looks huge (see is_huge_task). Small tasks skip the
board entirely so single-turn prompts stay byte-identical.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

BOARD_NAME = "nak_plan.md"


def board_path(session_id: Optional[str], workspace: Optional[str] = None) -> Optional[Path]:
    """artifacts/nak_plan.md for a session. None when no workspace/session."""
    if not session_id:
        return None
    try:
        from nak.plugins.tool.workspace import session_dir

        base = Path(workspace).expanduser().resolve() if workspace else None
        # session_dir(session, root) expects the workspace ROOT; when we only
        # have the project dir, its parent.parent is the root.
        if base is not None and base.name == "project":
            root = base.parents[1]
            sdir = session_dir(session_id, root=root)
        elif base is not None and base.name.startswith("session_"):
            sdir = base
        else:
            sdir = session_dir(session_id, root=base)
        return sdir / "artifacts" / BOARD_NAME
    except Exception:
        return None


def multitask_cfg() -> Dict[str, Any]:
    """Resolved [multitask] knobs. Never raises; defaults = disabled."""
    try:
        from nak.utils.config import get_multitask_enabled, get_multitask_max_agents
        from nak.utils.config import get_multitask_huge_tasks, get_multitask_huge_files
    except Exception:
        return {"enabled": False, "max_agents": 5, "huge_tasks": 5, "huge_files": 5}
    try:
        return {
            "enabled": bool(get_multitask_enabled()),
            "max_agents": int(get_multitask_max_agents()),
            "huge_tasks": int(get_multitask_huge_tasks()),
            "huge_files": int(get_multitask_huge_files()),
        }
    except Exception:
        return {"enabled": False, "max_agents": 5, "huge_tasks": 5, "huge_files": 5}


def is_huge_task(*, plan: Optional[List[str]] = None, files: Optional[List[str]] = None) -> bool:
    """Huge gate: plan steps >= huge_tasks OR files >= huge_files."""
    cfg = multitask_cfg()
    try:
        n_tasks = len(plan or [])
        n_files = len(files or [])
        return n_tasks >= int(cfg["huge_tasks"]) or n_files >= int(cfg["huge_files"])
    except Exception:
        return False


def _today() -> str:
    try:
        return _dt.date.today().isoformat()
    except Exception:
        return ""


def create_board(state: Any, slices: List[Dict[str, Any]]) -> Optional[str]:
    """Polaris/appointer creates the master board. Best-effort, never raises.

    slices: [{slice, tasks, agent, files, test}]
    Returns path str or None (disabled / not huge / no workspace).
    """
    try:
        cfg = multitask_cfg()
        if not cfg["enabled"]:
            return None
        plan = list(getattr(state, "plan", None) or [])
        files = [s.get("files", []) if isinstance(s, dict) else [] for s in slices]
        flat_files = [f for sub in files for f in (sub if isinstance(sub, list) else [sub])]
        if not is_huge_task(plan=plan or [s.get("slice", "") for s in slices], files=flat_files):
            return None
        bp = board_path(getattr(state, "session_id", None), getattr(state, "workspace", None))
        if bp is None:
            return None
        from nak.harness.project_memory import session_key

        key = session_key(getattr(state, "session_id", None))
        lines = [f"# Plan {key} — {len(slices)} slices — status: OPEN", "",
                 "| slice | tasks | agent | files | test | status | mind_key | updated |",
                 "|---|---|---|---|---|---|---|---|"]
        for s in slices:
            if not isinstance(s, dict):
                continue
            sl = str(s.get("slice", "?"))[:24]
            tasks = ",".join(str(t) for t in s.get("tasks", []))[:40] or "-"
            agent = str(s.get("agent", "?"))[:24]
            file_list = ",".join(str(f) for f in s.get("files", []))[:60] or "-"
            test = str(s.get("test", ""))[:40] or "-"
            lines.append(f"| {sl} | {tasks} | {agent} | {file_list} | {test} "
                         f"| [ ] todo | [project {key}] | {_today()} |")
        bp.parent.mkdir(parents=True, exist_ok=True)
        # create only (never overwrite an existing board mid-run)
        if not bp.exists():
            bp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(bp)
    except Exception:
        return None


def mark_slice(state: Any, slice_name: str, agent: str, ok: bool, note: str = "") -> bool:
    """Flip one row marker: [ ] -> [~] doing / [x] DONE|FAIL. Returns True if edited."""
    try:
        cfg = multitask_cfg()
        if not cfg["enabled"]:
            return False
        bp = board_path(getattr(state, "session_id", None), getattr(state, "workspace", None))
        if bp is None or not bp.exists():
            return False
        text = bp.read_text(encoding="utf-8", errors="replace")
        if slice_name not in text:
            return False
        tag = f"[x] DONE by {agent}: {note[:60]}" if ok else f"[!] FAIL by {agent}: {note[:60]}"
        new_lines: List[str] = []
        changed = False
        for line in text.splitlines():
            if line.startswith(f"| {slice_name} ") and "[ ]" in line:
                # replace status cell (7th col) — keep it simple: swap marker text
                line = line.replace("[ ] todo", tag).replace("[ ]", tag)
                changed = True
            new_lines.append(line)
        if changed:
            bp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return changed
    except Exception:
        return False


def read_board_snippet(state: Any, max_chars: int = 1500) -> str:
    """Bounded board snapshot for prompt injection ("" = none/disabled)."""
    try:
        if not multitask_cfg()["enabled"]:
            return ""
        bp = board_path(getattr(state, "session_id", None), getattr(state, "workspace", None))
        if bp is None or not bp.exists():
            return ""
        text = bp.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return ""
        return ("Plan board (artifacts/nak_plan.md):\n" + text[:max_chars])[: max_chars + 60]
    except Exception:
        return ""


def ensure_board_for_state(state: Any, files: Optional[List[str]] = None) -> Optional[str]:
    """Create the board from state.plan when huge + enabled + missing.

    One row per plan step: slice=part-<i>, tasks=[step], agent=state.agent
    or unassigned, files=workspace list (shared — refined by Polaris/appointer
    via edit_file later). Returns path or None. Never raises.
    """
    try:
        if not multitask_cfg()["enabled"]:
            return None
        bp = board_path(getattr(state, "session_id", None), getattr(state, "workspace", None))
        if bp is None or bp.exists():
            return str(bp) if bp is not None and bp.exists() else None
        plan = [str(p) for p in (getattr(state, "plan", None) or []) if str(p).strip()]
        if not is_huge_task(plan=plan, files=list(files or [])):
            return None
        agent = str(getattr(state, "agent", None) or "unassigned")
        slices = [{"slice": f"part-{chr(97 + i)}", "tasks": [step[:60]],
                   "agent": agent, "files": list(files or [])[:8], "test": "-"}
                  for i, step in enumerate(plan[:26])]
        return create_board(state, slices)
    except Exception:
        return None


__all__ = ["BOARD_NAME", "board_path", "multitask_cfg", "is_huge_task",
           "create_board", "ensure_board_for_state", "mark_slice", "read_board_snippet"]

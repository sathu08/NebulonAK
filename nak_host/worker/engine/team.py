"""Team selection + planner policy (no threads, no worker construction).

A team is a named roster, not a worker and not a hierarchy. Dispatch stays in
``dispatch.py``; this module only answers:
  * which team does this worker mean? (explicit name or its default)
  * is the planner inside or outside that team?
  * which assignments are allowed by the roster?
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .registry import get_team


def _teams_file(workers_file: str | Path) -> Path:
    return Path(workers_file).expanduser().parent / "teams.json"


def team_by_name(team: str,
                 workers_file: Optional[str | Path] = None) -> Dict[str, Any]:
    """Load an explicit team by name (no worker needed).

    Used when the planner itself comes from ``team.leader``: there is no
    worker yet to resolve a default team from. Raises ValueError when the
    name is empty or unknown.
    """
    requested = str(team or "").strip()
    if not requested:
        raise ValueError("no team name given")
    if workers_file:
        return get_team(requested, teams_file=_teams_file(workers_file),
                        workers_file=workers_file)
    return get_team(requested)


def resolve_team(worker: Any, team: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve an explicit team, else the worker's single default team.

    A worker with multiple teams must be explicit: there is no safe default.
    Returns a normalized team dict, or None when no team applies.
    """
    entry = getattr(worker, "entry", {}) or {}
    requested = str(team or "").strip()
    if not requested:
        names = [str(n).strip() for n in (entry.get("teams") or [])
                 if str(n).strip()]
        if len(names) > 1:
            raise ValueError(
                f"worker {entry.get('name')!r} belongs to multiple teams "
                f"{names}; pass team="
            )
        requested = names[0] if names else ""
    if not requested:
        return None
    workers_file = getattr(worker, "_workers_file", None)
    if workers_file:
        return get_team(requested, teams_file=_teams_file(workers_file),
                        workers_file=workers_file)
    return get_team(requested)


def planner_outside(team: Optional[Dict[str, Any]], planner: str) -> bool:
    """True when the planner is not in the selected team's roster."""
    if not team:
        return False
    return str(planner or "").strip() not in set(team["members"])


def ensure_planner_allowed(team: Optional[Dict[str, Any]], planner: str) -> bool:
    """Return planner_outside, raising when the team forbids an outsider."""
    outside = planner_outside(team, planner)
    if outside and team and team.get("planner_must_be_member"):
        raise ValueError(
            f"team {team['name']!r}: planner {planner!r} must be a member"
        )
    return outside


def split_assignments(team: Optional[Dict[str, Any]], assigned: Iterable[Any],
                      exclude: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """Split assignments into roster-allowed names and blocked names.

    ``exclude`` is the planner: it stays in the allowed list so dispatch can
    report it as skipped (the planner already spoke) instead of blocked.
    """
    names: List[str] = []
    for raw in (assigned or []):
        name = str(raw or "").strip()
        if name and name not in names:
            names.append(name)
    if not team:
        return names, []
    members = set(team["members"])
    me = str(exclude or "").strip()
    allowed: List[str] = []
    blocked: List[str] = []
    for name in names:
        if name == me or name in members:
            allowed.append(name)
        else:
            blocked.append(name)
    return allowed, blocked

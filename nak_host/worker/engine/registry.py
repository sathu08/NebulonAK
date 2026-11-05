"""workers.json + teams.json loading, normalization and membership checks.

Membership authority: ``teams.json.members`` decides who can be dispatched.
A worker's ``team`` block is its default affiliation and must agree with the
team roster; mismatches fail loudly at load time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from nak_host.utils.constant import (
    DEFAULT_TEAMS_JSON as _DEFAULT_TEAMS,
    DEFAULT_WORKERS_JSON as _DEFAULT_WORKERS,
    WORKER_DECISION_TYPES as _VALID_DECISION_TYPES,
    WORKER_FORBIDDEN_KEYS as _FORBIDDEN,
    WORKER_IDENTITY_FIELDS as IDENTITY_FIELDS,
)

from .autonomy import _autonomy_from_crafts
from .identity import _default_identity


def _teams_path_for(workers_file: str | Path) -> Path:
    return Path(workers_file).expanduser().parent / "teams.json"


def _normalize_worker_teams(entry: Dict[str, Any], name: str) -> List[str]:
    """Normalize one-team ``team`` or multi-team ``teams`` into a list."""
    has_team = "team" in entry and entry["team"] is not None
    has_teams = "teams" in entry and entry["teams"] is not None
    if has_team and has_teams:
        raise ValueError(f"entry {name!r}: use either 'team' or 'teams', not both")
    if has_teams:
        raw = entry["teams"]
        if not isinstance(raw, list):
            raise ValueError(f"entry {name!r}: 'teams' must be a list")
        out: List[str] = []
        for t in raw:
            s = str(t or "").strip()
            if not s:
                raise ValueError(f"entry {name!r}: 'teams' entries must be non-empty strings")
            if s in out:
                raise ValueError(f"entry {name!r}: duplicate team {s!r}")
            out.append(s)
        return out
    if not has_team:
        return []
    team = entry["team"]
    if not isinstance(team, dict):
        raise ValueError(f"entry {name!r}: 'team' must be an object")
    enabled = team.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError(f"entry {name!r}: 'team.enabled' must be true/false")
    tname = team.get("name", "")
    if not isinstance(tname, str):
        raise ValueError(f"entry {name!r}: 'team.name' must be a string")
    tname = tname.strip()
    if enabled and not tname:
        raise ValueError(f"entry {name!r}: 'team.name' is required when team.enabled is true")
    return [tname] if enabled and tname else []


def _read_workers(workers_file: str | Path) -> List[Dict[str, Any]]:
    """Load + normalize every worker entry (no team cross-validation)."""
    try:
        raw = json.loads(Path(workers_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"workers file unreadable {workers_file}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("workers.json must be a JSON list")
    out = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict) or not str(e.get("name", "")).strip():
            raise ValueError(f"entry {i}: need a non-empty 'name'")
        name = str(e.get("name", "")).strip()
        identity = e.get("identity")
        if identity is None and ("path" in e or "class" in e):
            identity = _default_identity()  # legacy pointer style -> default Vizhi
        if not isinstance(identity, dict):
            raise ValueError(f"entry {name!r}: missing 'identity' object")
        unknown = [k for k in identity if k not in IDENTITY_FIELDS]
        if unknown:
            raise ValueError(f"entry {name!r}: bad identity fields {unknown}")
        bad = _FORBIDDEN.intersection({k.lower() for k in identity} | {k.lower() for k in e})
        if bad:
            raise ValueError(f"entry {name!r}: forbidden keys {sorted(bad)}")
        mem = e.get("memory")
        if not isinstance(mem, dict) or not isinstance(mem.get("enabled"), bool):
            raise ValueError(f"entry {name!r}: missing 'memory.enabled' true/false")
        norm = dict(e)
        # type is a free-form label owned by workers.json itself (see
        # _worker_types): any non-empty value loads, no closed set.
        norm["type"] = str(e.get("type", "coordinator")).strip() or "coordinator"
        if not norm["type"]:
            raise ValueError(f"entry {name!r}: 'type' must be a non-empty string")
        norm["identity"] = identity
        norm["memory"] = {"enabled": bool(mem.get("enabled"))}
        teams = _normalize_worker_teams(e, name)
        norm["teams"] = teams
        # Legacy single-team view kept for older callers/tests.
        legacy = e.get("team") if isinstance(e.get("team"), dict) else {}
        legacy_name = str(legacy.get("name", "") or "").strip()
        norm["team"] = {"enabled": bool(teams),
                        "name": teams[0] if teams else legacy_name}
        if "autonomy" in e and e["autonomy"] is not None:
            raise ValueError(f"entry {name!r}: 'autonomy' was removed — "
                             f"use 'crafts' instead")
        norm.setdefault("capabilities", ["identity"])
        if "prompt_file" in e and not str(e.get("prompt_file") or "").strip():
            raise ValueError(f"entry {name!r}: empty 'prompt_file'")
        # decision block: optional laya System-1 scoring + user-set policy.
        # Shape: {"enabled": bool, "model": "laya",
        #         "questions": [{name, type}, ...],
        #         "policy": {"min_confidence": 0.6, "max_risk": 2.0, ...}}
        # type: score | boolean/noul | choice. Invalid shape -> ValueError.
        # BOUNDARY: validated INLINE here — never `from nak_host.decision... import`.
        # Missing policy -> backfilled from defaults (mirrored in Settings +
        # nak_host/decision/policy.py) so old entries keep working.
        if "decision" in e and e["decision"] is not None:
            dec = e["decision"]
            if not isinstance(dec, dict):
                raise ValueError(f"entry {name!r}: 'decision' must be an object")
            qs = dec.get("questions", [])
            if not isinstance(qs, list):
                raise ValueError(f"entry {name!r}: 'decision.questions' must be a list")
            for q in qs:
                if not isinstance(q, dict) or not str(q.get("name", "")).strip():
                    raise ValueError(f"entry {name!r}: each decision question needs a 'name'")
                qtype = str(q.get("type", "")).strip().lower()
                if qtype not in _VALID_DECISION_TYPES:
                    raise ValueError(
                        f"entry {name!r}: bad decision type {qtype!r} "
                        f"(want one of {sorted(_VALID_DECISION_TYPES)})"
                    )
            pol = dec.get("policy")
            if pol is not None:
                if not isinstance(pol, dict):
                    raise ValueError(f"entry {name!r}: 'decision.policy' must be an object")
                for pk in ("min_confidence", "max_risk", "max_priority_auto"):
                    if pk in pol and not isinstance(pol[pk], (int, float)):
                        raise ValueError(f"entry {name!r}: decision.policy.{pk} must be a number")
                for pk in ("enabled", "clarify_on_needs_clarification", "escalate_on_high_risk"):
                    if pk in pol and not isinstance(pol[pk], bool):
                        raise ValueError(f"entry {name!r}: decision.policy.{pk} must be true/false")
            else:
                pol = {}
            merged = {
                "enabled": True, "min_confidence": 0.6, "max_risk": 2.0,
                "max_priority_auto": 2.5, "clarify_on_needs_clarification": True,
                "escalate_on_high_risk": True, **pol,
            }
            norm["decision"] = {**dec, "policy": merged}
        # crafts allowlist: which crafts (plan/assign/...) this worker may use.
        # crafts is the ONLY source of truth for behavior (no autonomy block).
        # Missing key = all crafts; explicit [] = assist (answer directly).
        # Craft existence is checked in nak_host.harness.craft (no import here).
        if "crafts" in e and e["crafts"] is not None:
            raw_crafts = e["crafts"]
            if not isinstance(raw_crafts, list):
                raise ValueError(f"entry {name!r}: 'crafts' must be a list")
            clean_crafts: List[str] = []
            for c in raw_crafts:
                s = str(c or "").strip().lower()
                if not s:
                    raise ValueError(f"entry {name!r}: 'crafts' entries must be non-empty strings")
                if s not in clean_crafts:
                    clean_crafts.append(s)
            norm["crafts"] = clean_crafts
        else:
            norm["crafts"] = []
        # Derived behavior (modes/limits) comes from crafts.json pins —
        # see _autonomy_from_crafts. Stored as norm["autonomy"] for
        # enforcement/rendering compat (crafts remains the editable truth).
        norm["autonomy"] = _autonomy_from_crafts(
            None if "crafts" not in e or e["crafts"] is None else norm["crafts"],
            workers_file, name)
        out.append(norm)
    names = [e["name"] for e in out]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate worker names: {names}")
    return out


def _read_teams(teams_file: str | Path) -> List[Dict[str, Any]]:
    """Load + normalize teams. Accepts {"teams": [...]} or a bare list."""
    try:
        raw = json.loads(Path(teams_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"teams file unreadable {teams_file}: {exc}") from exc
    if isinstance(raw, dict):
        if "teams" not in raw:
            raise ValueError("teams.json must contain a 'teams' list")
        raw = raw["teams"]
    if not isinstance(raw, list):
        raise ValueError("teams.json must be a JSON list or {'teams': [...]}")
    out: List[Dict[str, Any]] = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ValueError(f"team {i}: must be an object")
        name = str(t.get("name", "") or "").strip()
        if not name:
            raise ValueError(f"team {i}: need a non-empty 'name'")
        members_raw = t.get("members")
        if not isinstance(members_raw, list) or not members_raw:
            raise ValueError(f"team {name!r}: 'members' must be a non-empty list")
        members: List[str] = []
        for m in members_raw:
            s = str(m or "").strip()
            if not s:
                raise ValueError(f"team {name!r}: member names must be non-empty strings")
            if s in members:
                raise ValueError(f"team {name!r}: duplicate member {s!r}")
            members.append(s)
        leader = t.get("leader", members[0])
        if not isinstance(leader, str):
            raise ValueError(f"team {name!r}: 'leader' must be a string")
        leader = leader.strip() or members[0]
        if leader not in members:
            raise ValueError(f"team {name!r}: leader {leader!r} is not in members")
        shared = t.get("shared_memory", True)
        if not isinstance(shared, bool):
            raise ValueError(f"team {name!r}: 'shared_memory' must be true/false")
        parallel = t.get("parallel", True)
        if not isinstance(parallel, bool):
            raise ValueError(f"team {name!r}: 'parallel' must be true/false")
        mw = t.get("max_workers")
        if mw is not None and (isinstance(mw, bool) or not isinstance(mw, int) or mw < 1):
            raise ValueError(f"team {name!r}: 'max_workers' must be an integer >= 1")
        strict = t.get("planner_must_be_member", False)
        if not isinstance(strict, bool):
            raise ValueError(f"team {name!r}: 'planner_must_be_member' must be true/false")
        description = t.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"team {name!r}: 'description' must be a string")
        for key in ("created_by", "created_at"):
            if key in t and (not isinstance(t[key], str) or not t[key].strip()):
                raise ValueError(f"team {name!r}: {key!r} must be a non-empty string")
        norm = dict(t)
        norm.update({
            "name": name,
            "description": description.strip(),
            "leader": leader,
            "members": members,
            "shared_memory": shared,
            "parallel": parallel,
            "max_workers": mw,
            "planner_must_be_member": strict,
        })
        out.append(norm)
    names = [t["name"] for t in out]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate team names: {names}")
    return out


def _validate_membership(workers: List[Dict[str, Any]],
                         teams: List[Dict[str, Any]]) -> None:
    """Reject any worker/team roster disagreement."""
    by_worker = {w["name"]: w for w in workers}
    by_team = {t["name"]: t for t in teams}
    for t in teams:
        tname = t["name"]
        if tname in by_worker:
            raise ValueError(f"team name {tname!r} collides with a worker name")
        for m in t["members"]:
            if m not in by_worker:
                raise ValueError(f"team {tname!r}: unknown member {m!r}")
            declared = by_worker[m].get("teams") or []
            if not declared:
                legacy = by_worker[m].get("team") or {}
                if legacy.get("name"):
                    raise ValueError(
                        f"worker {m!r}: team {legacy['name']!r} is disabled "
                        f"but the worker is listed in {tname!r}"
                    )
                raise ValueError(
                    f"worker {m!r}: no team declared "
                    f"but is listed in {tname!r}"
                )
            if tname not in declared:
                raise ValueError(
                    f"worker {m!r} declares teams {declared!r} "
                    f"but is listed in {tname!r}"
                )
    for w in workers:
        for tname in w.get("teams") or []:
            if tname not in by_team:
                raise ValueError(f"worker {w['name']!r}: unknown team {tname!r}")
            if w["name"] not in by_team[tname]["members"]:
                raise ValueError(
                    f"worker {w['name']!r}: declares team {tname!r} "
                    f"but is not in its members"
                )


def _reject_orphan_teams(workers: List[Dict[str, Any]], teams_file: Path) -> None:
    """A worker declaring a team needs teams.json next to workers.json."""
    orphaned = [w["name"] for w in workers if w.get("teams")]
    if orphaned:
        raise ValueError(
            f"worker(s) declare a team but {teams_file} is missing: {orphaned}"
        )


def list_workers(workers_file: str | Path = _DEFAULT_WORKERS) -> List[Dict[str, Any]]:
    """Load workers and, when present, validate them against teams.json."""
    workers = _read_workers(workers_file)
    teams_file = _teams_path_for(workers_file)
    if teams_file.exists():
        _validate_membership(workers, _read_teams(teams_file))
    else:
        _reject_orphan_teams(workers, teams_file)
    return workers


def list_teams(teams_file: str | Path = _DEFAULT_TEAMS,
               workers_file: str | Path = _DEFAULT_WORKERS) -> List[Dict[str, Any]]:
    """Load teams and validate their rosters against workers.json."""
    teams = _read_teams(teams_file)
    _validate_membership(_read_workers(workers_file), teams)
    return teams


def get_team(name: str, teams_file: str | Path = _DEFAULT_TEAMS,
             workers_file: str | Path = _DEFAULT_WORKERS) -> Dict[str, Any]:
    """Return one normalized team or raise ValueError when unknown."""
    wanted = str(name or "").strip()
    for team in list_teams(teams_file, workers_file):
        if team["name"] == wanted:
            return team
    raise ValueError(f"unknown team {wanted!r}")

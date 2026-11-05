"""nak.worker — JSON-defined workers + ONE individual engine (worker.py).

workers.json holds WHO + WHAT (inline identity per entry). teams.json holds
named rosters and shared-memory policy. brain/ owns all LLM. Tools are
placeholders only (caller-passed). The harness is owned by nak/wiring.py and
is never passed into a worker — workers receive only a session id + approver.
"""

from nak.utils.constant import WORKER_IDENTITY_FIELDS as IDENTITY_FIELDS

from .engine.autonomy import _autonomy_from_crafts, _craft_modes, _worker_types
from .engine.dispatch import run_workers
from .engine.registry import get_team, list_teams, list_workers
from .engine.team import (
    ensure_planner_allowed,
    planner_outside,
    resolve_team,
    split_assignments,
    team_by_name,
)
from .engine.worker import GenericWorker

__all__ = [
    "GenericWorker", "list_workers", "list_teams", "get_team", "team_by_name",
    "resolve_team", "planner_outside", "ensure_planner_allowed",
    "split_assignments", "IDENTITY_FIELDS", "_autonomy_from_crafts",
    "_worker_types", "_craft_modes", "run_workers",
]

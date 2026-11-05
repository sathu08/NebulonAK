"""Worker engine: GenericWorker + peer dispatch, one mixin per concern.

Nothing here imports nak.brain / nak.decision / nak.harness.
"""

from .autonomy import (
    AutonomyMixin,
    _autonomy_from_crafts,
    _autonomy_lines,
    _craft_modes,
    _worker_types,
)
from .chat import ChatMixin
from .identity import IdentityMixin, _default_identity
from .memory import MemoryMixin
from .prompt import (
    _craft_lines,
    _crafts_from_entry,
    _load_prompt_template,
    _render_prompt_template,
)
from .registry import get_team, list_teams, list_workers
from .session import (
    _session_file_lock,
    _session_lock,
    _session_merge_guard,
    _session_turns,
    _strip_prompt_dump,
)
from .dispatch import run_workers
from .team import ensure_planner_allowed, resolve_team, split_assignments
from .triage import TriageMixin
from .worker import GenericWorker

__all__ = [
    "AutonomyMixin",
    "ChatMixin",
    "GenericWorker",
    "IdentityMixin",
    "MemoryMixin",
    "TriageMixin",
    "ensure_planner_allowed",
    "get_team",
    "list_teams",
    "list_workers",
    "resolve_team",
    "run_workers",
    "split_assignments",
    "_autonomy_from_crafts",
    "_autonomy_lines",
    "_craft_lines",
    "_craft_modes",
    "_crafts_from_entry",
    "_default_identity",
    "_load_prompt_template",
    "_render_prompt_template",
    "_session_file_lock",
    "_session_lock",
    "_session_merge_guard",
    "_session_turns",
    "_strip_prompt_dump",
    "_worker_types",
]

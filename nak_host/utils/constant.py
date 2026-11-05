"""Shared constants for NebulonAK_1 (single source of truth).

Decision constants moved here from nak/decision/ so worker / harness /
api read the same values without importing nak.decision (boundary rule:
nak.decision never imports worker/brain/harness; everyone may import
nak.utils.constant — it has no package deps).

Sections:
  workers   — fallback workers/tools + default workers.json path
              + identity fields/forbidden keys/decision types/
              behavior defaults + registry-unreadable fallbacks
  brain     — recall depth + LLM timeout floors (client-side only;
              provider/model stay in NebulonMD, never here)
  worker-rt — in-session tuning: recall depth, history slices,
              decision-input budgets (counts change behavior)
  triage    — triage cache TTL/max + endpoint liveness TTL/probe timeout
  decision  — default decision questions + timeout + policy defaults/types
  manifests — default manifests paths (tools / permissions / crafts / teams)
  sandbox   — denied names/suffixes/dirs + workspace history file/keep
  limits    — default tool-call limits
  terminal  — output cap, timeouts, binary allowlist, deny lists
  files     — file/observe read caps + directory listing cap
  plans     — plan filename
  crafts    — craft approvals + worker modes + default modes
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

# --- workers -----------------------------------------------------------
# Worker entries (nak/worker): identity shape, forbidden keys, decision
# question types, crafts-derived behavior defaults + unreadable-registry
# fallbacks. Single truth here — nak/worker/worker.py imports these
# (thin aliases kept there so old imports keep working).
FALLBACK_WORKERS: List[str] = ["vizhi"]
FALLBACK_TOOLS: List[str] = ["file"]
DEFAULT_WORKERS_JSON: Path = (
    Path(__file__).resolve().parents[1] / "manifests" / "workers.json"
)
WORKER_IDENTITY_FIELDS = (
    "role", "personality", "tone", "formality",
    "humor", "gender", "values", "do_not", "language",
)
WORKER_FORBIDDEN_KEYS = {"provider", "model", "api_key", "base_url", "llm", "greeting"}
WORKER_DECISION_TYPES = {"choice", "score", "boolean", "noul"}
WORKER_DEFAULT_AUTONOMY: Dict[str, Any] = {"modes": ["plan"], "max_steps": 3,
                                            "approval": "all", "auto_steps": 2}
WORKER_FALLBACK_TYPES: List[str] = ["coordinator", "researcher", "coder"]
WORKER_FALLBACK_MODES: List[str] = ["plan", "assign", "build"]
WORKER_FALLBACK_IDENTITY: Dict[str, Any] = {"role": "project_manager", "language": "en",
        "personality": ["Calm", "practical", "proactive"],
        "tone": "Friendly and professional", "formality": "professional",
        "humor": "none", "gender": "unspecified",
        "values": ["Quality", "deadlines", "transparency"], "do_not": []}
# NOTE: worker approval vocabulary is CRAFT_VALID_APPROVALS (crafts section
# below) — no separate copy. Worker default path is DEFAULT_WORKERS_JSON.

# --- brain (client-side tuning only) -------------------------------------
# No provider/model/keys here ever — those live in NebulonMD's config.
BRAIN_DEFAULT_TOP_K: int = 5
BRAIN_CHAT_TIMEOUT_FLOOR: float = 60.0
BRAIN_EVAL_TIMEOUT_FLOOR: float = 300.0

# --- worker runtime (in-session tuning) ----------------------------------
WORKER_RECALL_TOP_K: int = 3
WORKER_TRIAGE_HISTORY_TURNS: int = 6
WORKER_CHAT_HISTORY_TURNS: int = 12
WORKER_MEMORY_SNIPPET_CHARS: int = 400
WORKER_DECISION_TEXT_CHARS: int = 2000
WORKER_DECISION_ROLE_CHARS: int = 1000
WORKER_SESSION_TURNS: int = 10
WORKER_SESSION_SNIPPET_CHARS: int = 400
# Cap the number of per-session merge locks kept in memory (old ids drop
# out; a later turn simply re-creates its lock).
WORKER_SESSION_LOCKS_MAX: int = 512
# Title length for the shared session chat doc (first user turn).
WORKER_SESSION_TITLE_CHARS: int = 60
# P1 context budget: total characters of the injected "session <id> so far"
# block. Turns are taken most-recent-first until this budget is spent, so a
# long-lived doc can never blow up the prompt (oldest turns drop, newest
# survive). 0 disables the budget (only WORKER_SESSION_TURNS applies).
WORKER_SESSION_CONTEXT_CHARS: int = 3500
# P2 doc bound: when MERGING, keep at most this many turns in the shared
# session doc (oldest trimmed) so the stored transcript cannot grow without
# bound. 0 disables trimming.
WORKER_SESSION_DOC_MAX_TURNS: int = 200
# P3 write verification: after saving a merged session doc, read it back and
# retry the merge when the newly appended turns are not visible yet (Mind can
# serve a brief stale read right after a write). 0 disables verification.
WORKER_SESSION_SAVE_RETRIES: int = 2
# Backoff between save/read-back attempts (seconds). 0 = no sleep.
WORKER_SESSION_SAVE_BACKOFF: float = 0.2
# P2 cross-process merge lock: directory for advisory <hash>.lock files so
# parallel worker *processes* sharing a session key cannot interleave the
# read-modify-write. "" -> system temp dir. Set to "off" to disable (then
# only the in-process thread lock applies).
WORKER_SESSION_LOCK_DIR: str = ""
# P1 (opt-in) durable Mind sessions: promote a harness session key to a real
# Mind session (create_session + list_sessions metadata lookup) so Mind owns
# the running multi-turn transcript server-side. Off by default — the P0
# client-owned merge is the validated path; enable only with Mind's
# NMD_AGENT_DURABLE_SESSIONS=true for cross-restart durability. Falls back to
# client-owned automatically when the brain lacks the session APIs.
WORKER_SESSION_PROMOTE: bool = False

# --- triage / endpoint liveness -----------------------------------------
TRIAGE_CACHE_TTL: float = 60.0
TRIAGE_CACHE_MAX: int = 200
ENDPOINT_LIVE_TTL: float = 30.0
ENDPOINT_PROBE_TIMEOUT: float = 2.0
ENDPOINT_PROBE_TIMEOUT_FLOOR: float = 0.5
DECISION_TIMEOUT_FLOOR: float = 1.0

# --- decision ------------------------------------------------------------
DECISION_TIMEOUT: float = 30.0
DEFAULT_DECISION_QUESTIONS: List[Dict[str, str]] = [
    {"name": "priority", "type": "score"},
    {"name": "risk", "type": "score"},
    {"name": "needs_clarification", "type": "boolean"},
]
# Craft-triggered decision batches (per-step routing). Endpoint mode only is
# safe to parallelize without a per-call brain; LLM mode stays serial.
DECISION_MAX_PARALLEL: int = 4
DECISION_BATCH_TIMEOUT: float = 30.0
DECISION_SCOPES = ("once", "per_step")
DECISION_ASSIGNMENTS = ("tags_win", "decision_only")
DECISION_MODES = ("endpoint", "llm", "auto")
DECISION_ON_ERROR = ("block", "ask")
DECISION_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "scope": "per_step",
    "assignment": "tags_win",
    "mode": "auto",
    "parallel": True,
    "max_parallel": DECISION_MAX_PARALLEL,
    "timeout": 10.0,
    "on_error": "block",
}

# Built-in policy fallbacks (nak/utils/settings.py mirrors these as
# DEFAULT_POLICY_* for new-worker stamping; entry policy wins).
DEFAULT_POLICY: Dict[str, Any] = {
    "enabled": True,
    # Below this answer_confidence on worker/tool choice -> clarify, don't act.
    "min_confidence": 0.6,
    # laya score scale 0..3 (low/medium/high/critical). At/above -> escalate.
    "max_risk": 2.0,
    # At/above this priority -> escalate to human even if risk is low.
    "max_priority_auto": 2.5,
    # Hard gates (independent of scores).
    "clarify_on_needs_clarification": True,
    "escalate_on_high_risk": True,
    # Which triage keys must carry a *_confidence to be trusted.
    "confidence_keys": ["priority", "risk", "needs_clarification"],
}

POLICY_TYPES: Dict[str, Any] = {
    "enabled": bool,
    "min_confidence": (int, float),
    "max_risk": (int, float),
    "max_priority_auto": (int, float),
    "clarify_on_needs_clarification": bool,
    "escalate_on_high_risk": bool,
}

# --- manifests -----------------------------------------------------------
_MANIFESTS_DIR: Path = Path(__file__).resolve().parents[1] / "manifests"
DEFAULT_TOOLS_JSON: Path = _MANIFESTS_DIR / "tools.json"
DEFAULT_PERMISSIONS_JSON: Path = _MANIFESTS_DIR / "permissions.json"
DEFAULT_CRAFTS_JSON: Path = _MANIFESTS_DIR / "crafts.json"
DEFAULT_TEAMS_JSON: Path = _MANIFESTS_DIR / "teams.json"

# --- sandbox -------------------------------------------------------------
SANDBOX_DENIED_NAMES = {".env"}
SANDBOX_DENIED_SUFFIXES = {".key", ".pem"}
# NOTE: denied DIRS live ONLY in tools.json (each tool's sandbox.deny_dirs).
# sandbox.py loads + unions them (fail-closed refuse-all when unreadable).
WORKSPACE_HISTORY_FILENAME: str = "workspace_history.json"
WORKSPACE_HISTORY_KEEP: int = 1000

# --- limits --------------------------------------------------------------
DEFAULT_LIMITS: Dict[str, Any] = {
    "max_tool_calls": 30,
    "max_execution_time": 120.0,
    "max_file_size": 10485760,
}

# --- capability ----------------------------------------------------------
CAP_MAP: Dict[str, str] = {
    "read": "file.read",
    "write": "file.write",
    "delete": "file.delete",
    "execute": "system.execute",
}

# --- terminal ------------------------------------------------------------
TERMINAL_MAX_OUTPUT: int = 12000
TERMINAL_DEFAULT_TIMEOUT: int = 60
TERMINAL_MAX_TIMEOUT: int = 120
TERMINAL_ALLOW_BINARIES = {
    "uv", "python", "python3", "ls", "cat", "head", "tail",
    "wc", "echo", "mkdir", "touch", "pdfinfo", "pdftotext",
    "grep", "git",
}
# Read-only git subcommands (a coder needs status/diff/log, not push/commit).
TERMINAL_ALLOW_GIT_SUBCOMMANDS = {
    "status", "diff", "log", "show", "branch", "rev-parse", "ls-files",
    "blame", "describe", "remote",
}
TERMINAL_DENY_SUBSTRINGS = [
    "sudo", "su ", "doas", "mkfs", " dd ", "dd if=",
    "shutdown", "reboot", "halt", "poweroff",
    ":(){", "fork bomb",
    ".env", ".ssh/", ".git/",
]
TERMINAL_DENY_RM_PATTERNS = ["rm -rf /", "rm -rf ~", "rm -rf .", "rm -rf *"]

# --- files ---------------------------------------------------------------
FILE_READ_MAX_CHARS: int = 8000
FILE_LIST_MAX_ENTRIES: int = 500
OBSERVE_MAX_CHARS: int = 8000

# --- assessor tools ------------------------------------------------------
ASSERT_MAX_CHECKS: int = 200
ASSERT_MAX_VALUE_CHARS: int = 600
TRACE_DEFAULT_TAIL: int = 50
TRACE_MAX_LINES: int = 200
TRACE_MAX_MATCHES: int = 200
TRACE_MAX_CHARS: int = 8000
TRACE_MAX_LINE_CHARS: int = 600
SCORECARD_MAX_RULES: int = 100
REPORT_MAX_BYTES: int = 1000000
REPORT_DEFAULT_DIR: str = "output/reports"

# --- plans ---------------------------------------------------------------
PLAN_FILENAME: str = "nak_plan.md"

# --- crafts --------------------------------------------------------------
# Fallback-only: truth lives in JSON (craft modes in crafts.json via
# nak.worker._craft_modes; worker types in workers.json via
# nak.worker._worker_types). These apply only when registries are unreadable.
CRAFT_VALID_APPROVALS = ("all", "small", "none")
CRAFT_WORKER_MODES = ("assist", "plan", "assign", "build")
CRAFT_DEFAULT_MODES = ["plan"]
CRAFT_MIN_STEPS: int = 1
CRAFT_MAX_STEPS: int = 8

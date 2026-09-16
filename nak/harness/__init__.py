"""nak.harness -- NebulonAK harness runtime (agent + tool + lifecycle).

Phase 1: state + executor + runtime (pipeline is a thin wrapper over this).
Phase 2: ToolRegistry + workspace isolation + fs/exec tools.
Phase 3+: observer / verifier / recovery plug into runtime hooks.
"""

from .state import (
    HarnessState,
    ToolCallRecord,
    Observation,
    create_state,
    STATUS_RUNNING,
    STATUS_NEEDS_CONFIRM,
    STATUS_WAITING_USER,
    STATUS_DONE,
    STATUS_FAILED,
)
from .executor import AgentExecutor, ToolExecutor
from .tool_registry import ToolRegistry
from .observer import (
    inspect as observe_result,
    SUCCESS,
    FAILURE,
    TIMEOUT,
    PARTIAL_SUCCESS,
    NEEDS_USER,
)
from .events import log_event
from .verifier import Verifier
from .project_memory import (
    store_turn as store_project_memory,
    recall_for_turn as recall_project_memory,
    build_memory_text,
    session_key as project_session_key,
    is_enabled as project_memory_enabled,
)
from .recovery import (
    recover as recover_state,
    decide_strategy,
    build_fix_prompt,
    max_retries,
    RETRY,
    REPLAN,
    ESCALATE,
)
from .runtime import HarnessRuntime

__all__ = [
    "HarnessState",
    "ToolCallRecord",
    "Observation",
    "create_state",
    "STATUS_RUNNING",
    "STATUS_NEEDS_CONFIRM",
    "STATUS_WAITING_USER",
    "STATUS_DONE",
    "STATUS_FAILED",
    "AgentExecutor",
    "ToolExecutor",
    "ToolRegistry",
    "observe_result",
    "SUCCESS",
    "FAILURE",
    "TIMEOUT",
    "PARTIAL_SUCCESS",
    "NEEDS_USER",
    "log_event",
    "Verifier",
    "store_project_memory",
    "recall_project_memory",
    "build_memory_text",
    "project_session_key",
    "project_memory_enabled",
    "recover_state",
    "decide_strategy",
    "build_fix_prompt",
    "max_retries",
    "RETRY",
    "REPLAN",
    "ESCALATE",
    "HarnessRuntime",
]

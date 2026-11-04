"""harness.engine — ExecutionHarness that runs suggested tool calls.

Layout (one module per concern):
  permission.py: Permission (verdicts, load, deny, rule_for).
  approval.py: Approval (ask-verdict).
  capability.py: Capability (CAP_MAP, required_caps).
  sandbox.py: Sandbox + Session (no separate session.py by design).
  limits.py: Limits (DEFAULT_LIMITS, norm/check).
  registry.py: Registry (tools.json load).
  execution.py: Tool Execution (resolve + run).
  engine.py: ExecutionHarness orchestrator (ToolEngine = alias).
"""

from .approval import ask
from .engine import ExecutionHarness, ToolEngine
from .permission import (
    assert_worker_known,
    audit_tool_coverage,
    load_worker_names,
    load_worker_role,
    merge_entries,
    registry_coverage,
)

__all__ = ["ExecutionHarness", "ToolEngine", "ask",
           "assert_worker_known", "audit_tool_coverage", "load_worker_names",
           "load_worker_role", "merge_entries", "registry_coverage"]

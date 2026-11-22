"""nak_host.flow — user-built single-chain pipelines.

Runs on TOP of the existing engine (worker/harness/application) via
nak_host.wiring. This package never imports nak_host.api (boundary:
api -> flow -> wiring -> engine).

Pipeline types: prompt | tool | craft only, single chain (linear).
Built-in template name is "vetting". User data lives in
<pipeline_dir>/<slug>/pipeline.json (see flow.store). Execution core
(validate/order/run/approve) lives in flow.engine.
"""

from nak_host.flow.engine import (
    decide_approval,
    list_approvals,
    order_chain,
    run_definition,
    validate_definition,
)
from nak_host.flow.store import (
    builtin_vetting_template,
    get_definition,
    list_definitions,
    save_definition,
)

__all__ = [
    "builtin_vetting_template",
    "decide_approval",
    "get_definition",
    "list_approvals",
    "list_definitions",
    "order_chain",
    "run_definition",
    "save_definition",
    "validate_definition",
]

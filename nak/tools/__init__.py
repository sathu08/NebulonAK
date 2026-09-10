"""nak.tools -- backward-compat shim (moved to nak.plugins.tool).

Canonical location:
    from nak.plugins.tool import NEBULONAK_TOOLS, execute_tool, ...

This path keeps working so existing code and examples do not break.
"""
from nak.plugins.tool import (
    NEBULONAK_TOOLS,
    NEBULON_TOOLS,
    execute_tool,
    get_tool_map,
)

__all__ = ["NEBULONAK_TOOLS", "NEBULON_TOOLS", "execute_tool", "get_tool_map"]

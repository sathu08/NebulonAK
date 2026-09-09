"""
nak.tools -- future LLM tools that run on the brain.

Each tool is a thin wrapper around nak.brain so LLM agents can call memory.

Example placeholder:

    from nak.tools import example_tool

Create new tools as modules, e.g. nak/tools/search_tool.py, and re-export here.
"""

from nak.tools.tools import NEBULONAK_TOOLS, NEBULON_TOOLS, execute_tool, get_tool_map

__all__ = ["NEBULONAK_TOOLS", "NEBULON_TOOLS", "execute_tool", "get_tool_map"]

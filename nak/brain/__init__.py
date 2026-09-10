"""
nak.brain -- the memory brain for NebulonAK on top of NebulonMind.

This package is intentionally thin: it mirrors NebulonMD's REST API
but is decoupled from nmd_host internals. All persistence goes
through NebulonMind's HTTP service (default localhost:9696).

Layout after reorg:
    nak/utils/config.py          -> NAKConfig loader (nebulonak.cfg + env overrides)
    nak/brain/client.py          -> Brain client (health, user, memory, search, chat)
    nak/plugins/tool/            -> generic tools (memory_tools, file_tool)
    nak/plugins/mcp/             -> MCP integration (planned)

For future LLM agents:
    from nak.plugins import PLUGIN_TOOLS, execute_plugin
    # or: from nak.plugins.tool import NEBULONAK_TOOLS, FILE_TOOLS
    from nak.brain import Brain
    brain = Brain()
    # pass NEBULONAK_TOOLS as `tools=` to your LLM, then route calls via execute_tool
"""

from nak.utils.config import NAKConfig, load_config
from .client import Brain, BrainError

__all__ = ["NAKConfig", "load_config", "Brain", "BrainError"]

"""
nak.brain -- the memory brain for NebulonAK on top of NebulonMind.

This package is intentionally thin: it mirrors NebulonMD's REST API
but is decoupled from nmd_host internals. All persistence goes
through NebulonMind's HTTP service (default localhost:9696).

Layout after reorg:
    nak/utils/config.py  -> NAKConfig loader (nebulonak.cfg + env overrides)
    nak/brain/client.py  -> Brain client (health, user, memory, search, chat)
    nak/tools/tools.py   -> OpenAI-compatible tool schemas that proxy to the brain

For future LLM agents:
    from nak.tools.tools import NEBULONAK_TOOLS, execute_tool
    # or: from nak.tools import NEBULONAK_TOOLS
    from nak.brain import Brain
    brain = Brain()
    # pass NEBULONAK_TOOLS as `tools=` to your LLM, then route calls via execute_tool
"""

from nak.utils.config import NAKConfig, load_config
from .client import Brain, BrainError

__all__ = ["NAKConfig", "load_config", "Brain", "BrainError"]

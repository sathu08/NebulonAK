"""
NAK - NebulonAK
===============
LLM Agent Kit that runs on NebulonMind.

nak.brain is the memory brain backed by NebulonMD (/api/NebulonMind).
nak.tools / nak.agents will hold future LLM tools & agents that call the brain.

Quick start:
    from nak.brain import Brain
    brain = Brain()  # reads ../nebulonak.cfg
    brain.ensure_user()
    print(brain.health())
    print(brain.chat("hello, remember I like Python"))
"""

__version__ = "1.0.0"

from nak.brain.client import Brain  # noqa: F401
from nak.utils.config import NAKConfig, load_config  # noqa: F401

__all__ = ["Brain", "NAKConfig", "load_config"]

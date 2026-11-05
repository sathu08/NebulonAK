"""nak.brain — the ONLY path to any LLM in NebulonAK_1.

Every method below is a plain HTTP call to NebulonMD. No inference,
no provider SDK, no API key handling for any LLM vendor happens here.
"""

from .client import Brain, BrainError
from .config import NAKConfig, load_config

__all__ = ["Brain", "BrainError", "NAKConfig", "load_config"]

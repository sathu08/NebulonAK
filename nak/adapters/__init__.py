"""nak.adapters — TaskStorePort implementations (no domain changes needed).

InMemoryTaskStore: process-local default for tests/offline use.
NebulonDBTaskStore: durable append-only store on NebulonDB itself.
"""

from .credentials_mind import (
    CredentialsError,
    MindCredentialsResolver,
    NDBCredentials,
)
from .executor_harness import HarnessExecutor
from .memory_nebulon import NebulonMemory
from .registry_json import JsonRegistry
from .store_memory import InMemoryTaskStore
from .store_nebulondb import NDBAuthError, NDBError, NebulonDBTaskStore

__all__ = [
    "CredentialsError",
    "HarnessExecutor",
    "InMemoryTaskStore",
    "JsonRegistry",
    "NebulonMemory",
    "MindCredentialsResolver",
    "NDBAuthError",
    "NDBCredentials",
    "NDBError",
    "NebulonDBTaskStore",
]

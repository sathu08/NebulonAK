"""nak_host.adapters — TaskStorePort implementations (no domain changes needed).

InMemoryTaskStore: process-local, test/offline-only reference implementation.
NebulonDBTaskStore: durable append-only prod store on NebulonDB itself
(use wiring.make_prod_store(); persistence loss is never silent).
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

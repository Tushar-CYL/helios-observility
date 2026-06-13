"""HELIOS Agent Runtime Intelligence — Python SDK (V1).

Public surface:

    helios.init(...)                      # wire OTel + ClickHouse
    with helios.agent_run(...) as run: ...
    with helios.mcp_call(...) as call: ...
    with helios.memory_op(...) as mem: ...
    helios.decision(src, dst, relation)
    helios.llm_call(id) / helios.final_answer(run)
    helios.flush() / helios.shutdown()
"""
from __future__ import annotations

from . import semconv
from .adapters import McpAdapter, McpAdapterRegistry, default_registry
from .api import (
    AgentRunHandle,
    McpHandle,
    MemoryHandle,
    NodeRef,
    agent_run,
    decision,
    final_answer,
    llm_call,
    mcp_call,
    memory_op,
)
from .runtime import flush, get_tracer, init, shutdown

__version__ = "0.1.0"

__all__ = [
    "init",
    "flush",
    "shutdown",
    "get_tracer",
    "agent_run",
    "mcp_call",
    "memory_op",
    "decision",
    "llm_call",
    "final_answer",
    "AgentRunHandle",
    "McpHandle",
    "MemoryHandle",
    "NodeRef",
    "McpAdapter",
    "McpAdapterRegistry",
    "default_registry",
    "semconv",
]

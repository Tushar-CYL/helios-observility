"""Versioned MCP adapters.

The Model Context Protocol is still evolving, so HELIOS never hard-codes one MCP
wire shape. Instead, each protocol version is handled by an adapter that knows
how to encode that version's arguments/results and classify its error payloads
into the HELIOS ``failure_mode`` enum. New versions register a new adapter; the
core instrumentation stays unchanged.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from . import semconv as S


class McpAdapter(ABC):
    """Normalizes a specific MCP protocol version into HELIOS fields."""

    protocol_version: str = ""

    @abstractmethod
    def encode_arguments(self, arguments: Any) -> str:
        """Serialize tool-call arguments to a (redactable) string."""

    @abstractmethod
    def encode_result(self, result: Any) -> str:
        """Serialize a tool result to a (redactable) string."""

    @abstractmethod
    def classify_error(self, error: Exception | dict | int) -> tuple[str, str, str]:
        """Return ``(failure_mode, error_type, message)`` for an MCP error."""


class Mcp20250618Adapter(McpAdapter):
    """Adapter for the 2025-06-18 MCP specification (JSON-RPC style)."""

    protocol_version = "2025-06-18"

    # JSON-RPC error code -> HELIOS failure_mode.
    _CODE_MAP = {
        -32001: "timeout",
        -32002: "permission_denied",
        -32603: "server_error",
        -32600: "bad_response",
        -32700: "bad_response",
    }

    def encode_arguments(self, arguments: Any) -> str:
        return json.dumps(arguments, default=str)

    def encode_result(self, result: Any) -> str:
        return json.dumps(result, default=str)

    def classify_error(self, error: Exception | dict | int) -> tuple[str, str, str]:
        if isinstance(error, TimeoutError):
            return "timeout", "TimeoutError", str(error) or "request timed out"
        if isinstance(error, PermissionError):
            return "permission_denied", "PermissionError", str(error)
        if isinstance(error, Exception):
            return "server_error", type(error).__name__, str(error)
        if isinstance(error, dict):  # JSON-RPC error object
            code = error.get("code")
            mode = self._CODE_MAP.get(code, "server_error")
            return mode, f"jsonrpc:{code}", error.get("message", "")
        if isinstance(error, int):
            return self._CODE_MAP.get(error, "server_error"), f"jsonrpc:{error}", ""
        return "server_error", "UnknownError", str(error)


class McpAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, McpAdapter] = {}

    def register(self, adapter: McpAdapter) -> None:
        if not adapter.protocol_version:
            raise ValueError("adapter must declare a protocol_version")
        self._adapters[adapter.protocol_version] = adapter

    def resolve(self, protocol_version: str) -> McpAdapter:
        if protocol_version in self._adapters:
            return self._adapters[protocol_version]
        if not self._adapters:
            raise LookupError("no MCP adapters registered")
        # Fall back to the latest known version rather than failing hard.
        latest = sorted(self._adapters)[-1]
        return self._adapters[latest]

    def versions(self) -> list[str]:
        return sorted(self._adapters)


# Default registry, pre-loaded with the current spec adapter.
default_registry = McpAdapterRegistry()
default_registry.register(Mcp20250618Adapter())

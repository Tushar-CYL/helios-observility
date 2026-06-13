"""RCA service configuration (env-driven)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    clickhouse_url: str = os.getenv("HELIOS_CLICKHOUSE_URL", "http://localhost:8123")
    clickhouse_user: str = os.getenv("HELIOS_CLICKHOUSE_USER", "helios")
    clickhouse_password: str = os.getenv("HELIOS_CLICKHOUSE_PASSWORD", "helios")
    clickhouse_db: str = os.getenv("HELIOS_CLICKHOUSE_DB", "helios")

    # Reasoner selection. "auto" uses Ollama when reachable, else heuristic.
    reasoner: str = os.getenv("HELIOS_RCA_REASONER", "auto")  # auto | ollama | heuristic
    ollama_endpoint: str = os.getenv("HELIOS_OLLAMA_ENDPOINT", "http://localhost:11434")
    ollama_model: str = os.getenv("HELIOS_OLLAMA_MODEL", "phi3.5")
    ollama_timeout: float = float(os.getenv("HELIOS_OLLAMA_TIMEOUT", "60"))


settings = Settings()

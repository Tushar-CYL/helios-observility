"""Minimal ClickHouse HTTP writer (standard library only).

Used by the HELIOS span processor to insert the four structured records. Kept
dependency-free on purpose so the SDK adds no transitive ClickHouse driver.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class ClickHouseWriter:
    def __init__(
        self,
        url: str = "http://localhost:8123",
        database: str = "helios",
        user: str = "helios",
        password: str = "helios",
        timeout: float = 15.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._database = database
        self._user = user
        self._password = password
        self._timeout = timeout

    def insert(self, table: str, rows: list[dict]) -> None:
        """Insert rows into ``database.table`` using JSONEachRow."""
        if not rows:
            return
        params = {
            "user": self._user,
            "password": self._password,
            "database": self._database,
            # Accept ISO-8601 timestamps ("...Z") emitted by the SDK.
            "date_time_input_format": "best_effort",
            "query": f"INSERT INTO {self._database}.{table} FORMAT JSONEachRow",
        }
        body = "\n".join(json.dumps(r, default=str) for r in rows).encode("utf-8")
        req = urllib.request.Request(
            f"{self._url}/?{urllib.parse.urlencode(params)}",
            data=body,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise RuntimeError(f"ClickHouse insert into {table} failed: {detail}") from None

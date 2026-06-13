"""ClickHouse access for the RCA service (read + write, stdlib only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class ClickHouse:
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

    def _request(self, params: dict, body: bytes | None = None) -> str:
        base = {"user": self._user, "password": self._password, "database": self._database}
        base.update(params)
        url = f"{self._url}/?{urllib.parse.urlencode(base)}"
        req = urllib.request.Request(url, data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise RuntimeError(f"ClickHouse error: {detail}") from None

    def query_json(self, sql: str) -> list[dict]:
        """Run a SELECT and return rows as dicts (JSONEachRow)."""
        out = self._request(
            {
                "query": f"{sql} FORMAT JSONEachRow",
                # Allow ISO-8601 timestamp literals in WHERE clauses.
                "date_time_input_format": "best_effort",
            }
        )
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def insert_json(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        body = "\n".join(json.dumps(r, default=str) for r in rows).encode("utf-8")
        self._request(
            {
                "date_time_input_format": "best_effort",
                "query": f"INSERT INTO {self._database}.{table} FORMAT JSONEachRow",
            },
            body=body,
        )

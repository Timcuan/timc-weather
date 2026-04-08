from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH, GAMMA_BASE_URL, REQUEST_TIMEOUT_SECONDS
from utils import create_http_session, retry, utc_now_iso

logger = logging.getLogger(__name__)


class OutcomeResolver:
    def __init__(self) -> None:
        self.session = create_http_session()

    @retry()
    def _get_market(self, market_id: str) -> dict[str, Any] | None:
        for url in (f"{GAMMA_BASE_URL}/markets/{market_id}", f"{GAMMA_BASE_URL}/markets"):
            try:
                params = None
                if url.endswith("/markets"):
                    params = {"id": market_id, "limit": 1}
                resp = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, dict):
                    if payload.get("id"):
                        return payload
                    data = payload.get("data")
                    if isinstance(data, list) and data:
                        first = data[0]
                        if isinstance(first, dict):
                            return first
                if isinstance(payload, list) and payload:
                    first = payload[0]
                    if isinstance(first, dict):
                        return first
            except Exception:  # noqa: BLE001
                continue
        return None

    def sync_resolved_outcomes(self, limit: int = 200) -> int:
        rows = self._pending_rows(limit)
        if not rows:
            return 0

        updates: list[tuple[str, str, str]] = []
        for row_id, market_id in rows:
            market = self._get_market(market_id)
            if not market:
                continue

            outcome = self._extract_resolved_outcome(market)
            if not outcome:
                continue

            updates.append((outcome, utc_now_iso(), str(row_id)))

        if not updates:
            return 0

        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "UPDATE edge_logs SET actual_outcome = ?, resolved_at = ? WHERE id = ?",
                updates,
            )
            conn.commit()

        logger.info("Resolved outcome sync updated %s rows", len(updates))
        return len(updates)

    def _pending_rows(self, limit: int) -> list[tuple[int, str]]:
        today = datetime.now(timezone.utc).date().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT id, market_id
                FROM edge_logs
                WHERE actual_outcome IS NULL
                  AND market_id IS NOT NULL
                  AND resolve_date < ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (today, limit),
            ).fetchall()

        output: list[tuple[int, str]] = []
        for row in rows:
            row_id = int(row[0])
            market_id = str(row[1])
            if market_id:
                output.append((row_id, market_id))
        return output

    def _extract_resolved_outcome(self, market: dict[str, Any]) -> str | None:
        direct_keys = [
            "winningOutcome",
            "winner",
            "resolvedOutcome",
            "result",
        ]
        for key in direct_keys:
            value = market.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for nested_key in ("resolution", "resolutionData"):
            nested = market.get(nested_key)
            if not isinstance(nested, dict):
                continue
            for key in direct_keys:
                value = nested.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return None

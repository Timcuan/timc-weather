from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from config import DATA_DIR, DB_PATH

TRADES_DB = DATA_DIR / "trades.sqlite"
VECTOR_DB_DIR = DATA_DIR / "vector_db"


class MemoryStore:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)
        self.vector_collection: Any | None = None
        self._init_db()
        self._init_vector_db()

    def _init_db(self) -> None:
        with sqlite3.connect(TRADES_DB) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    market_id TEXT,
                    condition_id TEXT,
                    city_key TEXT,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    market_id TEXT,
                    condition_id TEXT,
                    status TEXT NOT NULL,
                    paper INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_executions_created_at ON executions(created_at)")
            conn.commit()

    def save_decision(self, market_id: str, condition_id: str, city_key: str, payload: dict[str, Any]) -> None:
        created_at = self._utc_now_iso()
        with sqlite3.connect(TRADES_DB) as conn:
            conn.execute(
                """
                INSERT INTO decisions (created_at, market_id, condition_id, city_key, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    market_id,
                    condition_id,
                    city_key,
                    json.dumps(payload, default=str),
                ),
            )
            conn.commit()
        self._upsert_vector(
            doc_id=f"decision:{market_id}:{created_at}",
            document=json.dumps(payload, default=str),
            metadata={
                "kind": "decision",
                "market_id": market_id,
                "condition_id": condition_id,
                "city_key": city_key,
                "created_at": created_at,
            },
        )

    def save_execution(
        self,
        market_id: str,
        condition_id: str,
        status: str,
        paper: bool,
        payload: dict[str, Any],
    ) -> None:
        created_at = self._utc_now_iso()
        with sqlite3.connect(TRADES_DB) as conn:
            conn.execute(
                """
                INSERT INTO executions (created_at, market_id, condition_id, status, paper, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    market_id,
                    condition_id,
                    status,
                    1 if paper else 0,
                    json.dumps(payload, default=str),
                ),
            )
            conn.commit()
        self._upsert_vector(
            doc_id=f"execution:{market_id}:{created_at}",
            document=json.dumps(payload, default=str),
            metadata={
                "kind": "execution",
                "market_id": market_id,
                "condition_id": condition_id,
                "status": status,
                "paper": bool(paper),
                "created_at": created_at,
            },
        )

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_vector_db(self) -> None:
        try:
            import chromadb  # type: ignore

            client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
            self.vector_collection = client.get_or_create_collection(name="weather_trading_memory")
        except Exception:
            self.vector_collection = None

    def _upsert_vector(self, doc_id: str, document: str, metadata: dict[str, Any]) -> None:
        if self.vector_collection is None:
            return
        embedding = self._simple_embedding(document)
        try:
            self.vector_collection.upsert(
                ids=[doc_id],
                documents=[document],
                metadatas=[metadata],
                embeddings=[embedding],
            )
        except Exception:
            return

    def _simple_embedding(self, text: str) -> list[float]:
        # Lightweight deterministic embedding to avoid runtime model dependencies.
        buckets = [0.0] * 8
        for idx, ch in enumerate(text.encode("utf-8")):
            buckets[idx % 8] += float(ch)
        total = sum(buckets) or 1.0
        return [v / total for v in buckets]

    def get_recent_trade_history(self, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT created_at, city_key, favorite_outcome, model_prob, market_price, edge, actual_outcome
                FROM edge_logs
                WHERE should_alert = 1
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        history: list[dict[str, Any]] = []
        for row in rows:
            history.append(
                {
                    "created_at": row[0],
                    "city_key": row[1],
                    "favorite_outcome": row[2],
                    "model_prob": row[3],
                    "market_price": row[4],
                    "edge": row[5],
                    "actual_outcome": row[6],
                    "won": (row[2] is not None and row[6] is not None and str(row[2]) == str(row[6])),
                }
            )
        return history

    def get_city_win_rate(self, city_key: str) -> float:
        with sqlite3.connect(DB_PATH) as conn:
            total_row = conn.execute(
                """
                SELECT COUNT(*) FROM edge_logs
                WHERE city_key = ? AND should_alert = 1 AND actual_outcome IS NOT NULL
                """,
                (city_key,),
            ).fetchone()
            win_row = conn.execute(
                """
                SELECT COUNT(*) FROM edge_logs
                WHERE city_key = ?
                  AND should_alert = 1
                  AND actual_outcome IS NOT NULL
                  AND favorite_outcome = actual_outcome
                """,
                (city_key,),
            ).fetchone()

        total = int(total_row[0]) if total_row else 0
        wins = int(win_row[0]) if win_row else 0
        if total == 0:
            return 0.0
        return wins / total

    def get_station_bias(self, city_key: str) -> float:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT model_prob, favorite_outcome, actual_outcome
                FROM edge_logs
                WHERE city_key = ?
                  AND should_alert = 1
                  AND actual_outcome IS NOT NULL
                ORDER BY id DESC
                LIMIT 100
                """,
                (city_key,),
            ).fetchall()

        if not rows:
            return 0.0

        realized = 0.0
        expected = 0.0
        count = 0
        for model_prob, favorite, actual in rows:
            try:
                expected += float(model_prob or 0.0)
            except (TypeError, ValueError):
                expected += 0.0
            realized += 1.0 if (favorite is not None and actual is not None and str(favorite) == str(actual)) else 0.0
            count += 1
        if count == 0:
            return 0.0
        return (realized / count) - (expected / count)

    def get_open_positions_count(self) -> int:
        with sqlite3.connect(TRADES_DB) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM executions
                WHERE status IN ('paper_executed', 'live_executed', 'live_executed_with_errors')
                  AND created_at >= datetime('now', '-24 hours')
                """
            ).fetchone()
        return int(row[0]) if row else 0

    def get_runtime_context(self, city_keys: set[str], history_limit: int = 20) -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            trade_rows = conn.execute(
                """
                SELECT created_at, city_key, favorite_outcome, model_prob, market_price, edge, actual_outcome
                FROM edge_logs
                WHERE should_alert = 1
                ORDER BY id DESC
                LIMIT ?
                """,
                (history_limit,),
            ).fetchall()

            recent_trades: list[dict[str, Any]] = []
            for row in trade_rows:
                recent_trades.append(
                    {
                        "created_at": row[0],
                        "city_key": row[1],
                        "favorite_outcome": row[2],
                        "model_prob": row[3],
                        "market_price": row[4],
                        "edge": row[5],
                        "actual_outcome": row[6],
                        "won": (row[2] is not None and row[6] is not None and str(row[2]) == str(row[6])),
                    }
                )

        with sqlite3.connect(TRADES_DB) as conn:
            open_positions_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM executions
                WHERE status IN ('paper_executed', 'live_executed', 'live_executed_with_errors')
                  AND created_at >= datetime('now', '-24 hours')
                """
            ).fetchone()
            open_positions = int(open_positions_row[0]) if open_positions_row else 0

        city_win_rates: dict[str, float] = {city_key: 0.0 for city_key in city_keys}
        city_station_bias: dict[str, float] = {city_key: 0.0 for city_key in city_keys}

        if city_keys:
            placeholders = ",".join("?" for _ in city_keys)
            params = tuple(city_keys)
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute(
                    f"""
                    SELECT city_key, model_prob, favorite_outcome, actual_outcome
                    FROM edge_logs
                    WHERE city_key IN ({placeholders})
                      AND should_alert = 1
                      AND actual_outcome IS NOT NULL
                    ORDER BY id DESC
                    LIMIT 1000
                    """,
                    params,
                ).fetchall()

            grouped: dict[str, list[tuple[Any, Any, Any]]] = {city_key: [] for city_key in city_keys}
            for city_key, model_prob, favorite, actual in rows:
                grouped.setdefault(str(city_key), []).append((model_prob, favorite, actual))

            for city_key in city_keys:
                city_rows = grouped.get(city_key, [])
                if not city_rows:
                    continue

                sample = city_rows[:100]
                total = len(sample)
                wins = 0
                realized = 0.0
                expected = 0.0
                for model_prob, favorite, actual in sample:
                    if favorite is not None and actual is not None and str(favorite) == str(actual):
                        wins += 1
                        realized += 1.0
                    try:
                        expected += float(model_prob or 0.0)
                    except (TypeError, ValueError):
                        expected += 0.0

                city_win_rates[city_key] = (wins / total) if total else 0.0
                city_station_bias[city_key] = ((realized / total) - (expected / total)) if total else 0.0

        return {
            "recent_trades": recent_trades,
            "open_positions": open_positions,
            "city_win_rates": city_win_rates,
            "city_station_bias": city_station_bias,
        }

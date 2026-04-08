from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TypeVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    DB_PATH,
    LOG_DIR,
    MAX_RETRIES,
    RETRY_MAX_SLEEP_SECONDS,
    RETRY_BACKOFF_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
    USER_AGENT,
)

F = TypeVar("F", bound=Callable[..., Any])
TELEGRAM_FAILURE_COOLDOWN_SECONDS = 300
_telegram_disabled_until_ts = 0.0
_telegram_session: requests.Session | None = None


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "bot.log"
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if root.handlers:
        return

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5)
    file_handler.setFormatter(formatter)

    root.addHandler(stream)
    root.addHandler(file_handler)


def retry(max_retries: int = MAX_RETRIES, base_delay: float = RETRY_BACKOFF_SECONDS) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt == max_retries:
                        break
                    backoff = base_delay * (2 ** (attempt - 1))
                    jitter = random.uniform(0, base_delay)
                    sleep_time = min(backoff + jitter, RETRY_MAX_SLEEP_SECONDS)
                    logging.warning(
                        "Retrying %s after error (attempt %s/%s): %s",
                        func.__name__,
                        attempt,
                        max_retries,
                        exc,
                    )
                    time.sleep(sleep_time)
            raise RuntimeError(f"{func.__name__} failed after {max_retries} attempts") from last_exc

        return wrapper  # type: ignore[return-value]

    return decorator


def create_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
    )
    adapter = HTTPAdapter(
        pool_connections=50,
        pool_maxsize=50,
        max_retries=Retry(
            total=0,
            connect=0,
            read=0,
            redirect=0,
            status=0,
            backoff_factor=0,
        ),
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def send_telegram(message: str) -> None:
    global _telegram_disabled_until_ts
    global _telegram_session

    if (
        not TELEGRAM_TOKEN
        or not TELEGRAM_CHAT_ID
        or TELEGRAM_TOKEN.startswith("replace_with_")
        or TELEGRAM_CHAT_ID.startswith("replace_with_")
    ):
        logging.info("Telegram credentials not configured; skipping notification")
        return

    now_ts = time.time()
    if now_ts < _telegram_disabled_until_ts:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        if _telegram_session is None:
            _telegram_session = create_http_session()
        response = _telegram_session.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        _telegram_disabled_until_ts = 0.0
    except Exception as exc:  # noqa: BLE001
        _telegram_disabled_until_ts = now_ts + TELEGRAM_FAILURE_COOLDOWN_SECONDS
        logging.warning(
            "Telegram send failed; suppressing sends for %ss: %s",
            TELEGRAM_FAILURE_COOLDOWN_SECONDS,
            exc,
        )


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def ensure_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_cache (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS edge_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                market_id TEXT,
                condition_id TEXT,
                city_key TEXT,
                resolve_date TEXT,
                favorite_outcome TEXT,
                model_prob REAL,
                market_price REAL,
                edge REAL,
                should_alert INTEGER NOT NULL,
                market_precision REAL,
                actual_outcome TEXT,
                resolved_at TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "edge_logs", "market_precision", "REAL")
        _ensure_column(conn, "edge_logs", "actual_outcome", "TEXT")
        _ensure_column(conn, "edge_logs", "resolved_at", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_cache_expires_at ON api_cache(expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_logs_created_at ON edge_logs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_logs_should_alert ON edge_logs(should_alert)")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in columns:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def cache_get(key: str) -> Any | None:
    now_ts = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        row = conn.execute(
            "SELECT value_json FROM api_cache WHERE key = ? AND expires_at >= ?",
            (key, now_ts),
        ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    expires_at = int(time.time()) + ttl_seconds
    value_json = json.dumps(value)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(
            """
            INSERT INTO api_cache (key, value_json, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                expires_at = excluded.expires_at,
                created_at = excluded.created_at
            """,
            (key, value_json, expires_at, utc_now_iso()),
        )
        conn.commit()


def log_edge(row: dict[str, Any]) -> None:
    log_edges_bulk([row])


def log_edges_bulk(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        payloads = [
            (
                utc_now_iso(),
                row.get("market_id"),
                row.get("condition_id"),
                row.get("city_key"),
                row.get("resolve_date"),
                row.get("favorite_outcome"),
                row.get("model_prob"),
                row.get("market_price"),
                row.get("edge"),
                1 if row.get("should_alert") else 0,
                row.get("market_precision"),
                row.get("actual_outcome"),
                row.get("resolved_at"),
                json.dumps(row, default=str),
            )
            for row in rows
        ]
        conn.executemany(
            """
            INSERT INTO edge_logs (
                created_at,
                market_id,
                condition_id,
                city_key,
                resolve_date,
                favorite_outcome,
                model_prob,
                market_price,
                edge,
                should_alert,
                market_precision,
                actual_outcome,
                resolved_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payloads,
        )
        conn.commit()

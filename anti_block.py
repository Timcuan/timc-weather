from __future__ import annotations

import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    ADVANCED_HEADERS_REFERERS,
    ADVANCED_SESSION_ROTATE_MINUTES,
    ADVANCED_SESSION_ROTATE_REQUESTS,
    ENABLE_ADVANCED_ANTI_BLOCK,
    ENABLE_PROXY_ROTATION,
    PROXY_URLS,
    REQUEST_JITTER_MAX_MS,
    REQUEST_JITTER_MIN_MS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)

try:
    from fake_useragent import UserAgent  # type: ignore
except Exception:  # noqa: BLE001
    UserAgent = None  # type: ignore[assignment]


class AdvancedSessionManager:
    """
    Anti-detection HTTP manager with:
    - User-Agent rotation
    - Full header rotation
    - Session rotation by count and time
    - Optional proxy rotation
    - Request jitter
    """

    def __init__(self) -> None:
        self.enabled = ENABLE_ADVANCED_ANTI_BLOCK
        self.rotate_every_requests = max(1, ADVANCED_SESSION_ROTATE_REQUESTS)
        self.rotate_every_minutes = max(1, ADVANCED_SESSION_ROTATE_MINUTES)
        self.jitter_min_ms = max(0, REQUEST_JITTER_MIN_MS)
        self.jitter_max_ms = max(self.jitter_min_ms, REQUEST_JITTER_MAX_MS)
        self.proxy_rotation_enabled = ENABLE_PROXY_ROTATION and bool(PROXY_URLS)
        self.proxy_pool = deque(PROXY_URLS)
        self.ua_provider = self._build_ua_provider()

        self._session: requests.Session | None = None
        self._session_created_at = datetime.now(timezone.utc)
        self._requests_in_session = 0
        self._last_proxy: str | None = None

        self._rotate_session(force=True)

    def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self.request(method, url, **kwargs)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            # Fallback pair: retry once using basic headers/session profile.
            self._rotate_session(force=True, basic_profile=True)
            response = self.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self._maybe_rotate_session()
        self._sleep_with_jitter()

        assert self._session is not None
        response = self._session.request(
            method=method.upper(),
            url=url,
            timeout=kwargs.pop("timeout", REQUEST_TIMEOUT_SECONDS),
            **kwargs,
        )
        self._requests_in_session += 1
        return response

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def _build_ua_provider(self) -> Any | None:
        if UserAgent is None:
            return None
        try:
            return UserAgent()
        except Exception:  # noqa: BLE001
            return None

    def _maybe_rotate_session(self) -> None:
        if self._session is None:
            self._rotate_session(force=True)
            return

        age_minutes = (datetime.now(timezone.utc) - self._session_created_at).total_seconds() / 60.0
        if self._requests_in_session >= self.rotate_every_requests or age_minutes >= self.rotate_every_minutes:
            self._rotate_session(force=True)

    def _rotate_session(self, force: bool = False, basic_profile: bool = False) -> None:
        if not force and self._session is not None:
            return

        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass

        session = requests.Session()
        session.headers.update(self._build_basic_headers() if basic_profile else self._build_headers())
        adapter = HTTPAdapter(
            pool_connections=30,
            pool_maxsize=30,
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

        if self.proxy_rotation_enabled:
            proxy = self._next_proxy()
            if proxy:
                session.proxies.update({"http": proxy, "https": proxy})
                self._last_proxy = proxy
        self._session = session
        self._session_created_at = datetime.now(timezone.utc)
        self._requests_in_session = 0

    def _next_proxy(self) -> str | None:
        if not self.proxy_pool:
            return None
        proxy = self.proxy_pool[0]
        self.proxy_pool.rotate(-1)
        return proxy

    def _build_headers(self) -> dict[str, str]:
        ua = self._pick_user_agent()
        accept_language = random.choice(
            [
                "en-US,en;q=0.9",
                "en-GB,en;q=0.8",
                "en-US,en;q=0.8,id;q=0.6",
                "en-US,en;q=0.9,ja;q=0.5",
            ]
        )
        sec_fetch_site = random.choice(["same-site", "none", "cross-site"])
        referer = random.choice(ADVANCED_HEADERS_REFERERS)
        dnt = random.choice(["1", "0"])
        return {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": accept_language,
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": random.choice(["no-cache", "max-age=0"]),
            "Pragma": "no-cache",
            "DNT": dnt,
            "Connection": "keep-alive",
            "Referer": referer,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": sec_fetch_site,
        }

    def _build_basic_headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }

    def _pick_user_agent(self) -> str:
        if not self.enabled:
            return USER_AGENT
        if self.ua_provider is not None:
            try:
                return str(self.ua_provider.random)
            except Exception:  # noqa: BLE001
                pass
        return random.choice(
            [
                USER_AGENT,
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            ]
        )

    def _sleep_with_jitter(self) -> None:
        if self.jitter_max_ms <= 0:
            return
        base_ms = random.uniform(self.jitter_min_ms, self.jitter_max_ms)
        # Add occasional bursty think-time to avoid deterministic intervals.
        if random.random() < 0.08:
            base_ms += random.uniform(100, 400)
        time.sleep(base_ms / 1000.0)

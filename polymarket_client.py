from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from anti_block import AdvancedSessionManager
from config import (
    CACHE_TTL_SECONDS,
    CLOB_BATCH_TOKEN_IDS,
    CLOB_BASE_URL,
    GAMMA_BASE_URL,
    GAMMA_PAGE_LIMIT,
    MAX_MARKETS_TO_SCAN,
    REQUEST_TIMEOUT_SECONDS,
    TARGET_CITIES,
)
from utils import cache_get, cache_set, create_http_session, retry

logger = logging.getLogger(__name__)


@dataclass
class WeatherMarket:
    market_id: str
    condition_id: str
    slug: str
    question: str
    city_key: str
    resolve_date: str
    outcomes: list[str]
    outcome_to_token_id: dict[str, str]
    temperature_precision: float = 1.0
    liquidity_usd: float = 0.0


class PolymarketClient:
    def __init__(self) -> None:
        self.http = AdvancedSessionManager()
        self.basic_session = create_http_session()

    @retry()
    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return self.http.request_json(
                "GET",
                url,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as adv_exc:  # noqa: BLE001
            logger.warning("Advanced session failed, fallback to basic session: %s", adv_exc)
            response = self.basic_session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()

    def get_active_weather_markets(self) -> list[WeatherMarket]:
        cache_key = "gamma:active_weather_markets"
        cached = cache_get(cache_key)
        if cached:
            return [WeatherMarket(**item) for item in cached]

        markets: list[WeatherMarket] = []
        offset = 0

        while len(markets) < MAX_MARKETS_TO_SCAN:
            params = {
                "limit": GAMMA_PAGE_LIMIT,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "archived": "false",
            }
            data = self._get_json(f"{GAMMA_BASE_URL}/markets", params=params)
            if not isinstance(data, list) or not data:
                break

            for raw in data:
                parsed = self._parse_weather_market(raw)
                if parsed:
                    markets.append(parsed)
                    if len(markets) >= MAX_MARKETS_TO_SCAN:
                        break

            if len(data) < GAMMA_PAGE_LIMIT:
                break
            offset += GAMMA_PAGE_LIMIT

        cache_set(cache_key, [market.__dict__ for market in markets], CACHE_TTL_SECONDS)
        logger.info("Fetched %s weather markets", len(markets))
        return markets

    def _parse_weather_market(self, raw: dict[str, Any]) -> WeatherMarket | None:
        question = (raw.get("question") or "").strip()
        if not question:
            return None

        lowered = question.lower()
        if "highest temperature" not in lowered:
            return None

        city_key = self._match_city(lowered)
        if not city_key:
            return None

        outcomes = self._parse_list_field(raw.get("outcomes"))
        token_ids = self._parse_list_field(raw.get("clobTokenIds"))

        if not outcomes or len(outcomes) != len(token_ids):
            return None

        end_date = self._extract_resolve_date(raw)
        if not end_date:
            return None

        condition_id = str(raw.get("conditionId") or "")
        market_id = str(raw.get("id") or "")
        slug = str(raw.get("slug") or "")

        if not condition_id or not market_id or not slug:
            return None

        outcome_to_token_id = {outcome: token for outcome, token in zip(outcomes, token_ids, strict=False)}

        return WeatherMarket(
            market_id=market_id,
            condition_id=condition_id,
            slug=slug,
            question=question,
            city_key=city_key,
            resolve_date=end_date,
            outcomes=outcomes,
            outcome_to_token_id=outcome_to_token_id,
            temperature_precision=self._infer_temperature_precision(outcomes),
            liquidity_usd=self._parse_liquidity(raw),
        )

    def _match_city(self, question_lower: str) -> str | None:
        for city_key, city in TARGET_CITIES.items():
            if any(alias in question_lower for alias in city.aliases):
                return city_key
        return None

    def _parse_list_field(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except json.JSONDecodeError:
                return [v.strip() for v in value.split(",") if v.strip()]
        return []

    def _extract_resolve_date(self, raw: dict[str, Any]) -> str | None:
        candidates = [
            raw.get("endDate"),
            raw.get("end_date_iso"),
            raw.get("startDate"),
        ]

        for value in candidates:
            if not value:
                continue
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return dt.astimezone(UTC).date().isoformat()
            except ValueError:
                continue

        return None

    def _infer_temperature_precision(self, outcomes: list[str]) -> float:
        decimal_found = False
        for outcome in outcomes:
            for number in re.findall(r"-?\d+(?:\.\d+)?", outcome):
                if "." in number:
                    decimal_found = True
                    break
            if decimal_found:
                break
        return 0.1 if decimal_found else 1.0

    def _parse_liquidity(self, raw: dict[str, Any]) -> float:
        candidates = [
            raw.get("liquidity"),
            raw.get("liquidityNum"),
            raw.get("clobLiquidity"),
        ]
        for value in candidates:
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def get_market_prices(self, market: WeatherMarket) -> dict[str, float]:
        token_ids = [token for token in market.outcome_to_token_id.values() if token]
        if not token_ids:
            return {}
        token_to_price = self.get_token_prices_bulk(token_ids)
        return {
            outcome: token_to_price[token_id]
            for outcome, token_id in market.outcome_to_token_id.items()
            if token_id in token_to_price
        }

    def get_prices_for_markets(self, markets: list[WeatherMarket]) -> dict[str, dict[str, float]]:
        token_ids: list[str] = []
        for market in markets:
            token_ids.extend([token for token in market.outcome_to_token_id.values() if token])

        token_to_price = self.get_token_prices_bulk(token_ids)

        market_prices: dict[str, dict[str, float]] = {}
        for market in markets:
            market_prices[market.market_id] = {
                outcome: token_to_price[token_id]
                for outcome, token_id in market.outcome_to_token_id.items()
                if token_id in token_to_price
            }
        return market_prices

    def get_token_prices_bulk(self, token_ids: list[str]) -> dict[str, float]:
        unique_tokens = list(dict.fromkeys(token_ids))
        if not unique_tokens:
            return {}

        token_to_price: dict[str, float] = {}
        for chunk in self._chunks(unique_tokens, CLOB_BATCH_TOKEN_IDS):
            params = {
                "token_ids": ",".join(chunk),
                "sides": ",".join(["BUY"] * len(chunk)),
            }

            try:
                data = self._get_json(f"{CLOB_BASE_URL}/prices", params=params)
                token_to_price.update(self._parse_prices_response(data))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Batch /prices failed for %s tokens, fallback to /price: %s", len(chunk), exc)
                token_to_price.update(self._fetch_prices_fallback(chunk))

        return token_to_price

    def _parse_prices_response(self, payload: Any) -> dict[str, float]:
        if isinstance(payload, dict):
            if "prices" in payload and isinstance(payload["prices"], list):
                return self._extract_token_prices_from_list(payload["prices"])
            if "token_id" in payload and "price" in payload:
                return {str(payload["token_id"]): float(payload["price"])}

        if isinstance(payload, list):
            return self._extract_token_prices_from_list(payload)

        return {}

    def _extract_token_prices_from_list(self, rows: list[Any]) -> dict[str, float]:
        output: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            token_id = row.get("token_id") or row.get("tokenId")
            price = row.get("price")
            if token_id is None or price is None:
                continue
            try:
                output[str(token_id)] = float(price)
            except (TypeError, ValueError):
                continue
        return output

    def _fetch_prices_fallback(self, token_ids: list[str]) -> dict[str, float]:
        prices: dict[str, float] = {}
        for token_id in token_ids:
            try:
                data = self._get_json(
                    f"{CLOB_BASE_URL}/price",
                    params={"token_id": token_id, "side": "BUY"},
                )
                if isinstance(data, dict) and "price" in data:
                    prices[token_id] = float(data["price"])
            except Exception:  # noqa: BLE001
                continue
        return prices

    def _chunks(self, values: list[str], size: int) -> list[list[str]]:
        return [values[i : i + size] for i in range(0, len(values), size)]

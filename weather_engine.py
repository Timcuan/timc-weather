from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any

import numpy as np

from config import (
    OPEN_METEO_ENSEMBLE_URL,
    OPEN_METEO_MODELS,
    REQUEST_TIMEOUT_SECONDS,
    WEATHER_CACHE_TTL_SECONDS,
)
from station_mapping import get_station_for_city
from utils import cache_get, cache_set, create_http_session, retry

logger = logging.getLogger(__name__)


class WeatherEngine:
    def __init__(self) -> None:
        self.session = create_http_session()

    @retry()
    def _get_json(self, params: dict[str, Any]) -> Any:
        response = self.session.get(
            OPEN_METEO_ENSEMBLE_URL,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    def get_bin_probabilities(self, city_key: str, target_date: str, precision: float = 1.0) -> dict[str, Any]:
        station = get_station_for_city(city_key)
        precision = float(precision or station.precision or 1.0)
        cache_key = f"weather:{station.key}:{target_date}:p{precision}"
        cached = cache_get(cache_key)
        if cached:
            return cached

        hourly_params = {
            "latitude": station.lat,
            "longitude": station.lon,
            "elevation": station.elevation,
            "start_date": target_date,
            "end_date": target_date,
            "timezone": "UTC",
            "hourly": "temperature_2m",
        }
        member_maxima = self._fetch_member_maxima_with_fallback_models(hourly_params, target_date)

        if not member_maxima:
            raise RuntimeError(f"No ensemble members parsed for {city_key} {target_date}")

        bins = [self._to_temperature_bin(temp_c, precision) for temp_c in member_maxima]
        counter = Counter(bins)
        total_members = sum(counter.values())

        all_probs = {self._format_bin_key(bin_temp, precision): count / total_members for bin_temp, count in sorted(counter.items())}
        favorite_bin = max(all_probs.items(), key=lambda x: x[1])[0]
        model_prob = all_probs[favorite_bin]

        result = {
            "city_key": city_key,
            "station_key": station.key,
            "station_name": station.name,
            "target_date": target_date,
            "favorite_bin": float(favorite_bin),
            "model_prob": float(model_prob),
            "all_probs": all_probs,
            "total_members": int(total_members),
            "member_maxima": [float(x) for x in member_maxima],
            "precision": precision,
        }
        cache_set(cache_key, result, WEATHER_CACHE_TTL_SECONDS)
        return result

    def _fetch_member_maxima_with_fallback_models(self, base_params: dict[str, Any], target_date: str) -> list[float]:
        model_candidates: list[list[str]] = [
            OPEN_METEO_MODELS,
            ["ecmwf_ifs_025", "gfs_seamless"],
            ["ecmwf_ifs_025"],
            ["gfs_seamless"],
        ]

        for models in model_candidates:
            params = {**base_params, "models": ",".join(models)}
            try:
                payload = self._get_json(params)
                member_maxima = self._extract_from_hourly_block(payload.get("hourly"), target_date)
                if member_maxima:
                    return member_maxima
            except Exception as exc:  # noqa: BLE001
                logger.warning("Open-Meteo fetch failed for models=%s: %s", models, exc)
                continue
        return []

    def _extract_member_maxima_for_date(self, payload: Any, target_date: str) -> list[float]:
        values: list[float] = []

        if isinstance(payload, list):
            for item in payload:
                values.extend(self._extract_member_maxima_for_date(item, target_date))
            return values

        if not isinstance(payload, dict):
            return values

        values.extend(self._extract_from_daily_block(payload.get("daily"), target_date))
        if values:
            return values

        values.extend(self._extract_from_hourly_block(payload.get("hourly"), target_date))
        return values

    def _extract_from_daily_block(self, daily: Any, target_date: str) -> list[float]:
        if not isinstance(daily, dict):
            return []

        time_axis = daily.get("time")
        if not isinstance(time_axis, list) or target_date not in time_axis:
            return []

        idx = time_axis.index(target_date)
        maxima: list[float] = []

        for key, series in daily.items():
            if key == "time":
                continue
            if "temperature_2m_max" not in key:
                continue
            if not isinstance(series, list) or idx >= len(series):
                continue
            value = series[idx]
            if value is None:
                continue
            try:
                maxima.append(float(value))
            except (TypeError, ValueError):
                continue

        return maxima

    def _extract_from_hourly_block(self, hourly: Any, target_date: str) -> list[float]:
        if not isinstance(hourly, dict):
            return []

        time_axis = hourly.get("time")
        if not isinstance(time_axis, list):
            return []

        date_indexes: list[int] = []
        for idx, ts in enumerate(time_axis):
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.date().isoformat() == target_date:
                date_indexes.append(idx)

        if not date_indexes:
            return []

        maxima: list[float] = []
        for key, series in hourly.items():
            if key == "time":
                continue
            if not key.startswith("temperature_2m"):
                continue
            if not isinstance(series, list):
                continue

            selected = [series[i] for i in date_indexes if i < len(series) and series[i] is not None]
            if not selected:
                continue

            try:
                maxima.append(float(np.max(selected)))
            except (TypeError, ValueError):
                continue

        return maxima

    def _to_temperature_bin(self, temperature_c: float, precision: float) -> float:
        scaled = float(temperature_c) / precision
        rounded = round(scaled) * precision
        decimals = 0 if precision >= 1.0 else 1
        return float(round(rounded, decimals))

    def _format_bin_key(self, value: float, precision: float) -> str:
        if precision >= 1.0:
            return str(int(round(value)))
        return f"{value:.1f}"

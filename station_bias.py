from __future__ import annotations

from dataclasses import dataclass

from config import ELEVATION_LAPSE_RATE_C_PER_M, STATION_HISTORICAL_BIAS_C
from station_mapping import CITY_TO_STATION_KEY, get_station_for_city


@dataclass(frozen=True)
class BiasAdjustment:
    city_key: str
    station_name: str
    raw_temperature_c: float
    elevation_delta_c: float
    historical_delta_c: float
    total_delta_c: float
    adjusted_temperature_c: float


def compute_elevation_delta_c(model_elevation_m: float, station_elevation_m: float) -> float:
    """
    Convert elevation mismatch to temperature correction.
    Positive delta means station is cooler than model grid and should be adjusted down.
    """
    elevation_diff_m = float(station_elevation_m) - float(model_elevation_m)
    return -elevation_diff_m * ELEVATION_LAPSE_RATE_C_PER_M


def get_historical_bias_delta_c(city_key: str) -> float:
    normalized = CITY_TO_STATION_KEY.get(city_key, city_key).lower()
    return float(STATION_HISTORICAL_BIAS_C.get(normalized, 0.0))


def build_bias_adjustment(
    city_key: str,
    raw_temperature_c: float,
    model_elevation_m: float | None = None,
    station_elevation_m: float | None = None,
    historical_bias_c: float | None = None,
) -> BiasAdjustment:
    station = get_station_for_city(city_key)
    model_elevation = float(model_elevation_m if model_elevation_m is not None else station.elevation)
    station_elevation = float(station_elevation_m if station_elevation_m is not None else station.elevation)
    elevation_delta = compute_elevation_delta_c(model_elevation, station_elevation)
    historical_delta = (
        float(historical_bias_c)
        if historical_bias_c is not None
        else get_historical_bias_delta_c(city_key)
    )
    total_delta = elevation_delta + historical_delta
    adjusted = float(raw_temperature_c) + total_delta
    return BiasAdjustment(
        city_key=city_key,
        station_name=station.name,
        raw_temperature_c=float(raw_temperature_c),
        elevation_delta_c=elevation_delta,
        historical_delta_c=historical_delta,
        total_delta_c=total_delta,
        adjusted_temperature_c=adjusted,
    )


def apply_station_bias(
    city_key: str,
    raw_temperature_c: float,
    model_elevation_m: float | None = None,
    station_elevation_m: float | None = None,
    historical_bias_c: float | None = None,
) -> float:
    adj = build_bias_adjustment(
        city_key=city_key,
        raw_temperature_c=raw_temperature_c,
        model_elevation_m=model_elevation_m,
        station_elevation_m=station_elevation_m,
        historical_bias_c=historical_bias_c,
    )
    return adj.adjusted_temperature_c


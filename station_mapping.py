from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StationConfig:
    key: str
    name: str
    lat: float
    lon: float
    elevation: float
    unit: str = "celsius"
    precision: float = 1.0
    source: str = "wunderground"


STATIONS: dict[str, StationConfig] = {
    "shenzhen": StationConfig(
        key="shenzhen",
        name="Shenzhen Bao'an Intl (ZGSZ)",
        lat=22.639,
        lon=113.811,
        elevation=4,
    ),
    "taipei": StationConfig(
        key="taipei",
        name="Taipei Songshan Airport",
        lat=25.069,
        lon=121.552,
        elevation=6,
    ),
    "seoul": StationConfig(
        key="seoul",
        name="Incheon Intl (RKSI)",
        lat=37.469,
        lon=126.451,
        elevation=7,
    ),
    "tokyo": StationConfig(
        key="tokyo",
        name="Tokyo Haneda (RJTT)",
        lat=35.549,
        lon=139.779,
        elevation=6,
    ),
    "hongkong": StationConfig(
        key="hongkong",
        name="Hong Kong Observatory (HKO)",
        lat=22.302,
        lon=114.174,
        elevation=32,
        precision=1.0,
        source="hko",
    ),
    "shanghai": StationConfig(
        key="shanghai",
        name="Shanghai Pudong Intl (ZSPD)",
        lat=31.1443,
        lon=121.8083,
        elevation=4,
    ),
}

CITY_TO_STATION_KEY = {
    "hong_kong": "hongkong",
}


def get_station_for_city(city_key: str) -> StationConfig:
    normalized = CITY_TO_STATION_KEY.get(city_key, city_key)
    return STATIONS[normalized]

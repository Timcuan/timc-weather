from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BiasAdjustment:
    city_key: str
    station_name: str
    delta_c: float


def apply_station_bias(city_key: str, raw_temperature_c: float) -> float:
    """
    Phase 2 placeholder.

    Intended behavior:
    - Learn historical delta between Open-Meteo grid and airport station observations.
    - Apply city-specific correction before binning.
    """
    return raw_temperature_c

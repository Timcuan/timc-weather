from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _sanitize_secret(value: str) -> str:
    secret = (value or "").strip()
    lowered = secret.lower()
    if not secret:
        return ""
    if lowered.startswith("replace_with_") or lowered.startswith("your_"):
        return ""
    return secret


def _parse_city_bias_map(raw: str) -> dict[str, float]:
    output: dict[str, float] = {}
    for item in raw.split(","):
        if ":" not in item:
            continue
        city, delta = item.split(":", 1)
        city_key = city.strip().lower()
        if not city_key:
            continue
        try:
            output[city_key] = float(delta.strip())
        except ValueError:
            output[city_key] = 0.0
    return output

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "cache.sqlite"

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Thread strategy thresholds
MIN_MODEL_PROB = 0.70
MAX_MARKET_PRICE = 0.08
INSURANCE_PCT = 0.18
EDGE_MIN = 0.25

SCAN_INTERVAL_MINUTES = 15
SCAN_JITTER_MIN_SECONDS = 10
SCAN_JITTER_MAX_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 8
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 0.8
CACHE_TTL_SECONDS = 10 * 60
WEATHER_CACHE_TTL_SECONDS = 30 * 60
RETRY_MAX_SLEEP_SECONDS = 8.0
HEALTHCHECK_INTERVAL_MINUTES = 60
RESOLUTION_SYNC_INTERVAL_MINUTES = 30

MAX_MARKETS_TO_SCAN = 200
GAMMA_PAGE_LIMIT = 100
WEATHER_WORKERS = 8
CLOB_BATCH_TOKEN_IDS = int(os.getenv("CLOB_BATCH_TOKEN_IDS", "35"))

POLYMARKET_URL = "https://polymarket.com"

LOCAL_TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Jakarta"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ADMIN_IDS = {
    admin_id.strip()
    for admin_id in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
    if admin_id.strip()
}
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "rules").strip().lower()
_llm_model_env = os.getenv("LLM_MODEL", "").strip()
if _llm_model_env:
    LLM_MODEL = _llm_model_env
elif LLM_PROVIDER == "gemini":
    LLM_MODEL = "gemini-2.5-pro"
else:
    LLM_MODEL = "claude-3-5-sonnet-latest"
LLM_API_KEY = _sanitize_secret(os.getenv("LLM_API_KEY", ""))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.25"))
LLM_TOP_P = float(os.getenv("LLM_TOP_P", "0.95"))
LLM_TOP_K = int(os.getenv("LLM_TOP_K", "40"))
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024"))
LLM_REQUEST_TIMEOUT_SECONDS = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "12"))
LLM_HTTP_RETRY_ATTEMPTS = int(os.getenv("LLM_HTTP_RETRY_ATTEMPTS", "1"))

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
USDC_CONTRACT_ADDRESS = os.getenv("USDC_CONTRACT_ADDRESS", "")
POLYMARKET_EXCHANGE_ADDRESS = os.getenv("POLYMARKET_EXCHANGE_ADDRESS", "")

STARTING_EQUITY_USD = float(os.getenv("STARTING_EQUITY_USD", "1000"))
MIN_LLM_CONFIDENCE = float(os.getenv("MIN_LLM_CONFIDENCE", "0.82"))
KELLY_MIN_FRACTION = float(os.getenv("KELLY_MIN_FRACTION", "0.02"))
KELLY_MAX_FRACTION = float(os.getenv("KELLY_MAX_FRACTION", "0.04"))
MAX_POSITION_PER_MARKET_PCT = float(os.getenv("MAX_POSITION_PER_MARKET_PCT", "0.04"))
DAILY_LOSS_CAP_PCT = float(os.getenv("DAILY_LOSS_CAP_PCT", "0.05"))
CIRCUIT_BREAKER_CONSECUTIVE_LOSSES = int(os.getenv("CIRCUIT_BREAKER_CONSECUTIVE_LOSSES", "3"))
CIRCUIT_BREAKER_PAUSE_HOURS = int(os.getenv("CIRCUIT_BREAKER_PAUSE_HOURS", "24"))
NEAR_RESOLUTION_HOURS = int(os.getenv("NEAR_RESOLUTION_HOURS", "12"))
NO_SPREAD_MAX_PROB = float(os.getenv("NO_SPREAD_MAX_PROB", "0.08"))
NO_SPREAD_BIN_STEPS = tuple(
    int(step.strip())
    for step in os.getenv("NO_SPREAD_BIN_STEPS", "2,3").split(",")
    if step.strip()
)
MIN_MARKET_LIQUIDITY_USD = float(os.getenv("MIN_MARKET_LIQUIDITY_USD", "10000"))
MAX_SIZE_TO_LIQUIDITY_RATIO = float(os.getenv("MAX_SIZE_TO_LIQUIDITY_RATIO", "0.02"))
MAX_ORDER_SLIPPAGE_PCT = float(os.getenv("MAX_ORDER_SLIPPAGE_PCT", "0.08"))

ENABLE_ADVANCED_ANTI_BLOCK = os.getenv("ENABLE_ADVANCED_ANTI_BLOCK", "true").lower() == "true"
ADVANCED_SESSION_ROTATE_REQUESTS = int(os.getenv("ADVANCED_SESSION_ROTATE_REQUESTS", "8"))
ADVANCED_SESSION_ROTATE_MINUTES = int(os.getenv("ADVANCED_SESSION_ROTATE_MINUTES", "10"))
REQUEST_JITTER_MIN_MS = int(os.getenv("REQUEST_JITTER_MIN_MS", "20"))
REQUEST_JITTER_MAX_MS = int(os.getenv("REQUEST_JITTER_MAX_MS", "120"))
ENABLE_PROXY_ROTATION = os.getenv("ENABLE_PROXY_ROTATION", "false").lower() == "true"
PROXY_URLS = [p.strip() for p in os.getenv("PROXY_URLS", "").split(",") if p.strip()]
ADVANCED_HEADERS_REFERERS = [
    r.strip()
    for r in os.getenv(
        "ADVANCED_HEADERS_REFERERS",
        "https://polymarket.com,https://google.com,https://x.com,https://www.wunderground.com",
    ).split(",")
    if r.strip()
]

ELEVATION_LAPSE_RATE_C_PER_M = float(os.getenv("ELEVATION_LAPSE_RATE_C_PER_M", "0.0065"))
STATION_HISTORICAL_BIAS_C = _parse_city_bias_map(
    os.getenv(
        "STATION_HISTORICAL_BIAS_C",
        "shenzhen:0.0,seoul:0.0,tokyo:0.0,taipei:0.0,hongkong:0.0",
    )
)

OPEN_METEO_MODELS = [
    "ecmwf_ifs025",
    "gfs025",
]


@dataclass(frozen=True)
class CityConfig:
    key: str
    display_name: str
    lat: float
    lon: float
    airport_station: str
    aliases: tuple[str, ...]


TARGET_CITIES: dict[str, CityConfig] = {
    "shenzhen": CityConfig(
        key="shenzhen",
        display_name="Shenzhen",
        lat=22.5431,
        lon=114.0579,
        airport_station="Shenzhen Bao'an International Airport (ZGSZ)",
        aliases=("shenzhen",),
    ),
    "taipei": CityConfig(
        key="taipei",
        display_name="Taipei",
        lat=25.0330,
        lon=121.5654,
        airport_station="Taiwan Taoyuan International Airport",
        aliases=("taipei",),
    ),
    "seoul": CityConfig(
        key="seoul",
        display_name="Seoul",
        lat=37.5665,
        lon=126.9780,
        airport_station="Incheon International Airport",
        aliases=("seoul", "incheon"),
    ),
    "tokyo": CityConfig(
        key="tokyo",
        display_name="Tokyo",
        lat=35.6762,
        lon=139.6503,
        airport_station="Haneda / Narita (verify per market)",
        aliases=("tokyo", "haneda", "narita"),
    ),
    "hong_kong": CityConfig(
        key="hong_kong",
        display_name="Hong Kong",
        lat=22.3193,
        lon=114.1694,
        airport_station="Hong Kong International Airport",
        aliases=("hong kong", "hk"),
    ),
    "shanghai": CityConfig(
        key="shanghai",
        display_name="Shanghai",
        lat=31.2304,
        lon=121.4737,
        airport_station="Shanghai Pudong / Hongqiao (verify per market)",
        aliases=("shanghai",),
    ),
}

USER_AGENT = (
    "Mozilla/5.0 (compatible; PolymarketWeatherBot/1.1; "
    "+https://github.com/Timcuan/timc-weather)"
)

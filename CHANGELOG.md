# Changelog

All notable changes to this project are documented in this file.

## [2.3.0] - 2026-04-08

### Added
- `anti_block.py` with `AdvancedSessionManager`:
  - real-browser user-agent rotation (`fake-useragent` + fallback pool),
  - full header rotation,
  - request jitter,
  - session rotation by request count/time,
  - optional proxy rotation.

### Changed
- Integrated advanced anti-block session manager into:
  - `polymarket_client.py` (Gamma/CLOB),
  - `weather_engine.py` (Open-Meteo).
- Upgraded station-bias pipeline:
  - implemented `station_bias.py`,
  - weather ensemble maxima now corrected using elevation + historical station bias before binning.
- Hardened Gemini decisioning:
  - temperature clamped to 0.2-0.3,
  - added self-critique veto gate for unsafe decisions.
- Strengthened risk sizing:
  - added `KELLY_MIN_FRACTION`,
  - daily loss cap now anchored to day-start equity.
- Upgraded execution path:
  - added `execute_trade()` path,
  - pre-trade live quote check,
  - per-order slippage guard for live execution.

### Fixed
- Reduced risk of degraded execution during unstable market conditions via slippage skip logic.
- Improved resilience against endpoint-level soft throttling and fingerprint reuse by rotating HTTP session identity.

### Dependencies
- Added `fake-useragent`.

## [2.2.0] - 2026-04-08

### Added
- Full project scaffold for Polymarket weather bot (scanner, weather engine, edge logic, risk, executor, memory, resolver).
- Station-based mapping via `station_mapping.py`.
- Gemini decision integration with structured JSON output and safety fallback.
- Telegram command controls (`/status`, `/pause`, `/resume`, `/paper`, `/live`, `/sync`, `/help`).
- Systemd deployment template and install script.
- Operational docs: `UPDATE.md`, `UPDATE_PLAN.md`, `UPDATE_PLAN_2.1.md`, `VALIDATION_CHECKLIST.md`, `GEMINI_TUNING.md`.

### Changed
- Unified LLM configuration to generic `LLM_*` variables (provider-agnostic).
- Core price threshold aligned to strategy (`MAX_MARKET_PRICE=0.08`).
- Weather probability engine uses hourly ensemble max with station precision-aware binning.
- Main loop now snapshots runtime context once per cycle to reduce repeated DB queries.

### Fixed
- Resolved major runtime bottlenecks for 24/7 VPS operation:
  - Reduced per-market SQLite context lookups by batching cycle context.
  - Added Telegram fail-open cooldown to avoid retry storms when network fails.
  - Added Gemini fail-open cooldown and bounded request timeout/retries.
  - Reused HTTP sessions for command polling and telegram send.

### Security
- Added `.gitignore` for secrets and runtime artifacts (`.env`, sqlite DBs, logs, vector DB, `.venv`).

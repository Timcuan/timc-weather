# Changelog

All notable changes to this project are documented in this file.

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


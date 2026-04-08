from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import schedule

from config import (
    HEALTHCHECK_INTERVAL_MINUTES,
    LLM_MODEL,
    LLM_PROVIDER,
    NEAR_RESOLUTION_HOURS,
    RESOLUTION_SYNC_INTERVAL_MINUTES,
    SCAN_INTERVAL_MINUTES,
    SCAN_JITTER_MAX_SECONDS,
    SCAN_JITTER_MIN_SECONDS,
    TARGET_CITIES,
    TELEGRAM_ADMIN_IDS,
    WEATHER_WORKERS,
)
from executor import TradeExecutor
from edge_calculator import EdgeCalculator
from llm_decision import LLMDecisionEngine
from memory_store import MemoryStore
from outcome_resolver import OutcomeResolver
from polymarket_client import PolymarketClient, WeatherMarket
from risk_manager import RiskManager
from utils import create_http_session, ensure_db, log_edges_bulk, send_telegram, setup_logging
from weather_engine import WeatherEngine

logger = logging.getLogger(__name__)


class ScannerApp:
    def __init__(self) -> None:
        self.polymarket = PolymarketClient()
        self.weather = WeatherEngine()
        self.edge_calculator = EdgeCalculator()
        self.risk_manager = RiskManager()
        self.llm_engine = LLMDecisionEngine()
        self.executor = TradeExecutor()
        self.memory = MemoryStore()
        self.outcome_resolver = OutcomeResolver()
        self.command_session = create_http_session()
        self._scan_lock = threading.Lock()
        self.manual_paused = False
        self.command_offset = 0
        self._last_circuit_alert_at: datetime | None = None
        self.scanned_today = 0
        self.alerts_today = 0
        self.last_day = datetime.now(timezone.utc).date()

    def run_scanner(self) -> None:
        self.process_telegram_commands()

        if self.manual_paused:
            logger.info("Scanner is manually paused; skipping cycle")
            return

        if not self._scan_lock.acquire(blocking=False):
            logger.warning("Previous scanner cycle still running; skipping this tick")
            return

        cycle_start = time.perf_counter()
        logger.info("Starting scanner cycle")
        try:
            self._roll_daily_counters_if_needed()
            try:
                markets = self.polymarket.get_active_weather_markets()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed fetching active weather markets: %s", exc)
                return
            logger.info("Scanning %s candidate weather markets", len(markets))
            self.scanned_today += len(markets)

            alert_count = 0
            payload_rows: list[dict[str, object]] = []

            weather_map = self._prefetch_weather(markets)
            price_map = self.polymarket.get_prices_for_markets(markets)
            runtime_context = self.memory.get_runtime_context(
                city_keys={market.city_key for market in markets},
                history_limit=20,
            )
            recent_trades = runtime_context.get("recent_trades", [])
            open_positions = int(runtime_context.get("open_positions", 0))
            city_win_rates = runtime_context.get("city_win_rates", {})
            city_station_bias = runtime_context.get("city_station_bias", {})
            trade_history_summary = self._summarize_trade_history(recent_trades)

            for market in markets:
                try:
                    weather_data = weather_map.get((market.city_key, market.resolve_date, market.temperature_precision))
                    if not weather_data:
                        continue

                    prices = price_map.get(market.market_id, {})
                    edge = self.edge_calculator.detect_edge(market, weather_data, prices)
                    execution_payload = None
                    llm_confidence = None
                    llm_action = None
                    risk_reason = None

                    payload_rows.append(
                        {
                            "market_id": market.market_id,
                            "condition_id": market.condition_id,
                            "city_key": market.city_key,
                            "resolve_date": market.resolve_date,
                            "favorite_outcome": edge.favorite_outcome if edge else None,
                            "model_prob": edge.favorite_model_prob if edge else None,
                            "market_price": edge.favorite_market_price if edge else None,
                            "edge": edge.edge if edge else None,
                            "should_alert": bool(edge),
                            "market_precision": market.temperature_precision,
                            "execution_status": None,
                            "llm_confidence": None,
                            "risk_reason": None,
                        }
                    )

                    if edge:
                        city_win_rate = float(city_win_rates.get(market.city_key, 0.0))
                        station_bias = float(city_station_bias.get(market.city_key, 0.0))
                        context = {
                            "current_time": datetime.now(timezone.utc).isoformat(),
                            "market_question": market.question,
                            "city": TARGET_CITIES[market.city_key].display_name,
                            "station_name": weather_data.get("station_name"),
                            "end_date": market.resolve_date,
                            "hours_to_resolve": self._hours_to_resolve(market.resolve_date),
                            "model_prob": edge.favorite_model_prob,
                            "market_price": edge.favorite_market_price,
                            "edge": edge.edge,
                            "tail_prob": self._tail_probability(edge),
                            "near_resolution": self._is_near_resolution(market.resolve_date),
                            "favorite_bin": weather_data.get("favorite_bin"),
                            "all_probs_dict": weather_data.get("all_probs", {}),
                            "prices_dict": prices,
                            "bankroll_usd": self.risk_manager.equity_usd,
                            "open_positions": open_positions,
                            "open_positions_summary": f"{open_positions} open positions",
                            "recent_trades": recent_trades,
                            "trade_history_summary": trade_history_summary,
                            "city_win_rate": city_win_rate,
                            "win_rate": city_win_rate * 100.0,
                            "station_bias": station_bias,
                            "liquidity": market.liquidity_usd,
                            "suggested_size_usdc": self.risk_manager.equity_usd * 0.02,
                        }
                        llm = self.llm_engine.evaluate(context)
                        llm_confidence = llm.confidence
                        llm_action = llm.action

                        risk = self.risk_manager.evaluate(
                            {
                                "llm_confidence": llm.confidence,
                                "model_prob": edge.favorite_model_prob,
                                "market_price": edge.favorite_market_price,
                                "edge": edge.edge,
                                "liquidity_usd": market.liquidity_usd,
                            }
                        )
                        risk_reason = risk.reason
                        if "circuit breaker active" in risk_reason.lower():
                            now = datetime.now(timezone.utc)
                            if self._last_circuit_alert_at is None or (
                                now - self._last_circuit_alert_at
                            ).total_seconds() >= 3600:
                                self._safe_notify("🚨 Circuit breaker active. Trading paused by risk manager.")
                                self._last_circuit_alert_at = now

                        decision_payload = {
                            "llm_action": llm.action,
                            "llm_confidence": llm.confidence,
                            "llm_rationale": llm.rationale,
                            "llm_bin": llm.bin_label,
                            "llm_size_usdc": llm.size_usdc,
                            "llm_insurance_pct": llm.insurance_pct,
                            "risk_approved": risk.approved,
                            "risk_reason": risk.reason,
                            "position_size_usd": risk.position_size_usd,
                            "kelly_fraction": risk.kelly_fraction,
                            "city_win_rate": city_win_rate,
                            "station_bias": station_bias,
                            "market_liquidity_usd": market.liquidity_usd,
                        }
                        self.memory.save_decision(
                            market.market_id,
                            market.condition_id,
                            market.city_key,
                            decision_payload,
                        )

                        if llm.action == "BUY" and risk.approved:
                            execution_payload = self.executor.execute(
                                market=market,
                                edge=edge,
                                market_prices=prices,
                                position_size_usd=risk.position_size_usd,
                                include_no_spread=llm.include_no_spread,
                            )
                            self.memory.save_execution(
                                market.market_id,
                                market.condition_id,
                                execution_payload["status"],
                                execution_payload.get("paper", True),
                                execution_payload,
                            )

                        message = self.edge_calculator.build_telegram_message(edge, weather_data)
                        if llm_action:
                            message += (
                                f"\\n*LLM:* {llm_action} ({(llm_confidence or 0) * 100:.1f}%)"
                                f"\\n*Risk:* {risk_reason}"
                            )
                        if execution_payload:
                            message += f"\\n*Exec:* {execution_payload.get('status')}"

                        payload_rows[-1]["llm_confidence"] = llm_confidence
                        payload_rows[-1]["risk_reason"] = risk_reason
                        payload_rows[-1]["execution_status"] = (
                            execution_payload.get("status") if execution_payload else "skipped"
                        )
                        try:
                            send_telegram(message)
                            alert_count += 1
                        except Exception as exc:  # noqa: BLE001
                            logger.exception("Failed sending telegram for market %s: %s", market.market_id, exc)

                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed processing market %s (%s): %s", market.market_id, market.question, exc)

            log_edges_bulk(payload_rows)
            self.alerts_today += alert_count
            duration = time.perf_counter() - cycle_start
            logger.info(
                "Scanner cycle completed. alerts=%s markets=%s duration=%.2fs",
                alert_count,
                len(markets),
                duration,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled scanner error: %s", exc)
        finally:
            self._scan_lock.release()

    def run_scanner_with_jitter(self) -> None:
        jitter = random.randint(SCAN_JITTER_MIN_SECONDS, SCAN_JITTER_MAX_SECONDS)
        logger.info("Applying scan jitter: sleeping %ss before cycle", jitter)
        time.sleep(jitter)
        self.run_scanner()

    def send_healthcheck(self) -> None:
        self.process_telegram_commands()
        self._roll_daily_counters_if_needed()
        mode = "PAPER" if self.executor.paper else "LIVE"
        message = (
            "🫀 *Bot Healthcheck*\\n"
            f"- Status: alive\\n"
            f"- Mode: {mode}\\n"
            f"- Equity: ${self.risk_manager.equity_usd:.2f}\\n"
            f"- Markets scanned today: {self.scanned_today}\\n"
            f"- Alerts sent today: {self.alerts_today}\\n"
        )
        try:
            send_telegram(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed sending healthcheck: %s", exc)

    def process_telegram_commands(self) -> None:
        try:
            offset, commands = self._fetch_telegram_commands(self.command_offset)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping command poll due to error: %s", exc)
            return

        if not commands:
            self.command_offset = offset
            return

        for command in commands:
            lowered = command.strip().lower()
            if lowered == "/pause":
                self.manual_paused = True
                self._safe_notify("⏸️ Bot paused by command.")
            elif lowered == "/resume":
                self.manual_paused = False
                self._safe_notify("▶️ Bot resumed by command.")
            elif lowered == "/paper":
                self.executor.paper = True
                self._safe_notify("🧪 Mode switched to PAPER.")
            elif lowered == "/live":
                if self.executor.live_ready:
                    self.executor.paper = False
                    self._safe_notify("⚠️ Mode switched to LIVE.")
                else:
                    self._safe_notify("❌ LIVE mode unavailable. Check key/client init.")
            elif lowered == "/status":
                self._safe_notify(self._build_status_text())
            elif lowered == "/help":
                self._safe_notify(
                    "🧭 *Commands*\\n"
                    "/status - ringkasan bot\\n"
                    "/equity - equity & pnl harian\\n"
                    "/risk - risk state\\n"
                    "/pause - pause scanner\\n"
                    "/resume - resume scanner\\n"
                    "/paper - set paper mode\\n"
                    "/live - set live mode\\n"
                    "/sync - trigger resolved-outcome sync\\n"
                    "/help - daftar command"
                )
            elif lowered == "/equity":
                self._safe_notify(
                    "💰 *Equity*\\n"
                    f"- equity_usd: ${self.risk_manager.equity_usd:.2f}\\n"
                    f"- realized_pnl_today: ${self.risk_manager.realized_pnl_today:.2f}"
                )
            elif lowered == "/risk":
                pause_until = (
                    self.risk_manager.pause_until.isoformat()
                    if self.risk_manager.pause_until
                    else "none"
                )
                self._safe_notify(
                    "🛡️ *Risk*\\n"
                    f"- consecutive_losses: {self.risk_manager.consecutive_losses}\\n"
                    f"- pause_until: {pause_until}\\n"
                    f"- manual_paused: {self.manual_paused}"
                )
            elif lowered == "/sync":
                self.sync_resolved_outcomes()
                self._safe_notify("🔄 Resolved-outcome sync executed.")

        self.command_offset = offset

    def _safe_notify(self, message: str) -> None:
        try:
            send_telegram(message)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to send command reply: %s", exc)

    def _build_status_text(self) -> str:
        mode = "PAPER" if self.executor.paper else "LIVE"
        pause_until = (
            self.risk_manager.pause_until.isoformat()
            if self.risk_manager.pause_until
            else "none"
        )
        open_positions = self.memory.get_open_positions_count()
        return (
            "📊 *Status*\\n"
            f"- paused: {self.manual_paused}\\n"
            f"- mode: {mode}\\n"
            f"- live_ready: {self.executor.live_ready}\\n"
            f"- scanned_today: {self.scanned_today}\\n"
            f"- alerts_today: {self.alerts_today}\\n"
            f"- equity_usd: ${self.risk_manager.equity_usd:.2f}\\n"
            f"- pnl_today: ${self.risk_manager.realized_pnl_today:.2f}\\n"
            f"- open_positions: {open_positions}\\n"
            f"- risk_pause_until: {pause_until}"
        )

    def _fetch_telegram_commands(self, offset: int) -> tuple[int, list[str]]:
        from config import REQUEST_TIMEOUT_SECONDS, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

        if (
            not TELEGRAM_TOKEN
            or not TELEGRAM_CHAT_ID
            or TELEGRAM_TOKEN.startswith("replace_with_")
            or TELEGRAM_CHAT_ID.startswith("replace_with_")
        ):
            return offset, []

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        params = {
            "offset": offset,
            "timeout": 0,
            "allowed_updates": ["message"],
        }
        resp = self.command_session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result", []) if isinstance(data, dict) else []
        next_offset = offset
        commands: list[str] = []
        for item in result:
            update_id = int(item.get("update_id", 0))
            next_offset = max(next_offset, update_id + 1)
            message = item.get("message", {})
            if not isinstance(message, dict):
                continue
            chat = message.get("chat", {})
            chat_id = str(chat.get("id", ""))
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue
            from_user = message.get("from", {})
            user_id = str(from_user.get("id", ""))
            if TELEGRAM_ADMIN_IDS and user_id not in TELEGRAM_ADMIN_IDS:
                continue
            text = str(message.get("text", "")).strip()
            if text.startswith("/"):
                commands.append(text.split()[0])

        return next_offset, commands

    def sync_resolved_outcomes(self) -> None:
        try:
            updated = self.outcome_resolver.sync_resolved_outcomes(limit=200)
            if updated > 0:
                logger.info("Resolved outcomes synced: %s rows", updated)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Resolved outcome sync failed: %s", exc)

    def _roll_daily_counters_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today == self.last_day:
            return
        self._safe_notify(
            "📅 *Daily Summary*\\n"
            f"- day: {self.last_day.isoformat()}\\n"
            f"- markets_scanned: {self.scanned_today}\\n"
            f"- alerts_sent: {self.alerts_today}\\n"
            f"- equity_usd: ${self.risk_manager.equity_usd:.2f}\\n"
            f"- pnl_today: ${self.risk_manager.realized_pnl_today:.2f}"
        )
        self.last_day = today
        self.scanned_today = 0
        self.alerts_today = 0

    def _tail_probability(self, edge: object) -> float:
        outcome_probs = getattr(edge, "outcome_probs", {}) or {}
        if not outcome_probs:
            return 1.0
        favorite = getattr(edge, "favorite_outcome", None)
        return max(0.0, 1.0 - float(outcome_probs.get(favorite, 0.0)))

    def _is_near_resolution(self, resolve_date: str) -> bool:
        try:
            resolve_dt = datetime.fromisoformat(resolve_date).replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        delta_hours = (resolve_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return 0 <= delta_hours <= NEAR_RESOLUTION_HOURS

    def _hours_to_resolve(self, resolve_date: str) -> float:
        try:
            resolve_dt = datetime.fromisoformat(resolve_date).replace(tzinfo=timezone.utc)
        except ValueError:
            return -1.0
        delta_hours = (resolve_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return round(delta_hours, 2)

    def _summarize_trade_history(self, recent_trades: list[dict[str, object]]) -> str:
        if not recent_trades:
            return "no recent trades"

        lines: list[str] = []
        for trade in recent_trades[:10]:
            city = str(trade.get("city_key", "?"))
            outcome = str(trade.get("favorite_outcome", "?"))
            edge = trade.get("edge", 0.0)
            won = bool(trade.get("won", False))
            try:
                edge_fmt = f"{float(edge):.3f}"
            except (TypeError, ValueError):
                edge_fmt = "0.000"
            lines.append(f"{city}:{outcome}:edge={edge_fmt}:won={won}")
        return " | ".join(lines)

    def _prefetch_weather(self, markets: list[WeatherMarket]) -> dict[tuple[str, str, float], dict]:
        unique_targets = {(market.city_key, market.resolve_date, market.temperature_precision) for market in markets}
        weather_map: dict[tuple[str, str, float], dict] = {}
        if not unique_targets:
            return weather_map

        with ThreadPoolExecutor(max_workers=WEATHER_WORKERS) as executor:
            future_map = {
                executor.submit(self.weather.get_bin_probabilities, city_key, resolve_date, precision): (
                    city_key,
                    resolve_date,
                    precision,
                )
                for city_key, resolve_date, precision in unique_targets
            }
            for future in as_completed(future_map):
                city_key, resolve_date, precision = future_map[future]
                try:
                    weather_map[(city_key, resolve_date, precision)] = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Failed weather fetch for %s %s (precision=%s): %s",
                        city_key,
                        resolve_date,
                        precision,
                        exc,
                    )

        return weather_map


def main() -> None:
    setup_logging()
    ensure_db()

    app = ScannerApp()
    logger.info("LLM runtime provider=%s model=%s", LLM_PROVIDER, LLM_MODEL)

    try:
        send_telegram(
            "✅ *Polymarket Weather Bot started*\n"
            f"Scanner aktif tiap 15 menit.\n"
            f"LLM: `{LLM_PROVIDER}` / `{LLM_MODEL}`"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Startup telegram failed: %s", exc)

    app.run_scanner_with_jitter()

    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(app.run_scanner_with_jitter)
    schedule.every(HEALTHCHECK_INTERVAL_MINUTES).minutes.do(app.send_healthcheck)
    schedule.every(RESOLUTION_SYNC_INTERVAL_MINUTES).minutes.do(app.sync_resolved_outcomes)
    logger.info("Scheduler running every %s minutes", SCAN_INTERVAL_MINUTES)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from config import (
    CIRCUIT_BREAKER_CONSECUTIVE_LOSSES,
    CIRCUIT_BREAKER_PAUSE_HOURS,
    DAILY_LOSS_CAP_PCT,
    KELLY_MAX_FRACTION,
    MAX_SIZE_TO_LIQUIDITY_RATIO,
    MAX_POSITION_PER_MARKET_PCT,
    MIN_MARKET_LIQUIDITY_USD,
    MIN_LLM_CONFIDENCE,
    STARTING_EQUITY_USD,
)


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    position_size_usd: float
    kelly_fraction: float


class RiskManager:
    def __init__(self) -> None:
        self.equity_usd = STARTING_EQUITY_USD
        self.realized_pnl_today = 0.0
        self.consecutive_losses = 0
        self.pause_until: datetime | None = None
        self._pnl_day = datetime.now(timezone.utc).date()

    def evaluate(self, candidate: dict[str, Any]) -> RiskDecision:
        self._roll_day_if_needed()

        now = datetime.now(timezone.utc)
        if self.pause_until and now < self.pause_until:
            return RiskDecision(False, f"circuit breaker active until {self.pause_until.isoformat()}", 0.0, 0.0)

        llm_conf = float(candidate.get("llm_confidence", 0.0))
        if llm_conf < MIN_LLM_CONFIDENCE:
            return RiskDecision(False, "llm confidence below threshold", 0.0, 0.0)

        if self.realized_pnl_today <= -(self.equity_usd * DAILY_LOSS_CAP_PCT):
            return RiskDecision(False, "daily loss cap reached", 0.0, 0.0)

        model_prob = float(candidate.get("model_prob", 0.0))
        market_price = float(candidate.get("market_price", 1.0))
        edge = max(0.0, model_prob - market_price)
        liquidity_usd = float(candidate.get("liquidity_usd", 0.0))

        if market_price <= 0 or market_price >= 1:
            return RiskDecision(False, "invalid market price", 0.0, 0.0)
        if liquidity_usd < MIN_MARKET_LIQUIDITY_USD:
            return RiskDecision(False, "liquidity below minimum threshold", 0.0, 0.0)

        # Simplified Kelly for binary payout: b = (1-p)/p
        b = (1.0 - market_price) / market_price
        q = 1.0 - model_prob
        raw_kelly = max(0.0, (b * model_prob - q) / b) if b > 0 else 0.0
        kelly_fraction = min(raw_kelly, KELLY_MAX_FRACTION, MAX_POSITION_PER_MARKET_PCT)

        if kelly_fraction <= 0:
            return RiskDecision(False, "kelly <= 0", 0.0, 0.0)

        # Slightly downscale size if edge marginal.
        edge_scale = min(1.0, max(0.4, edge / 0.30))
        final_fraction = kelly_fraction * edge_scale
        position_size = max(1.0, self.equity_usd * final_fraction)
        if liquidity_usd > 0 and (position_size / liquidity_usd) > MAX_SIZE_TO_LIQUIDITY_RATIO:
            return RiskDecision(False, "slippage guard: size/liquidity too high", 0.0, 0.0)

        return RiskDecision(True, "approved", position_size, final_fraction)

    def record_trade_result(self, pnl_usd: float) -> None:
        self._roll_day_if_needed()
        self.realized_pnl_today += pnl_usd
        self.equity_usd += pnl_usd

        if pnl_usd < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        if self.consecutive_losses >= CIRCUIT_BREAKER_CONSECUTIVE_LOSSES:
            self.pause_until = datetime.now(timezone.utc) + timedelta(hours=CIRCUIT_BREAKER_PAUSE_HOURS)

    def _roll_day_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today == self._pnl_day:
            return
        self._pnl_day = today
        self.realized_pnl_today = 0.0
        self.consecutive_losses = 0

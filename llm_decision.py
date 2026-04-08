from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from config import (
    EDGE_MIN,
    LLM_API_KEY,
    LLM_HTTP_RETRY_ATTEMPTS,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_REQUEST_TIMEOUT_SECONDS,
    LLM_TEMPERATURE,
    LLM_TOP_K,
    LLM_TOP_P,
    MIN_LLM_CONFIDENCE,
    NO_SPREAD_MAX_PROB,
)

try:
    from langgraph.graph import END, START, StateGraph  # type: ignore
except Exception:  # noqa: BLE001
    StateGraph = None  # type: ignore[assignment]
    START = "START"  # type: ignore[assignment]
    END = "END"  # type: ignore[assignment]

logger = logging.getLogger(__name__)


GEMINI_SYSTEM_PROMPT = """You are an extremely disciplined, conservative Polymarket weather specialist.
Only trade when edge is clear.

Core rules:
- Trade only Highest Temperature markets.
- Use station-specific ensemble data.
- Buy YES only when model probability >= 70% and market price <= 0.08.
- Use cheap insurance 15-20% around +-1C and +-2C bins.
- Buy NO-spread on +2C / +3C bins when model probability < 8%.
- Prefer markets resolving <12 hours.
- If data is missing or ambiguous, SKIP.

Hard safety rules:
- Never invent probabilities, prices, or liquidity.
- Require confidence >= 0.82 AND edge >= 0.25 for any trade action.
- If uncertain, return SKIP.

Output strict JSON matching schema only.
"""


class TradingDecision(BaseModel):
    action: Literal["BUY_YES", "BUY_NO_SPREAD", "BUY_INSURANCE_ONLY", "SKIP"]
    target_bin: str | None = None
    size_usdc: float = Field(ge=0)
    insurance_pct: float = Field(ge=0, le=0.25)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=200)
    risk_notes: str = Field(default="")


@dataclass
class LLMDecision:
    action: str
    confidence: float
    rationale: str
    include_no_spread: bool
    bin_label: str
    size_usdc: float
    insurance_pct: float


class LLMDecisionEngine:
    def __init__(self) -> None:
        self.provider = (LLM_PROVIDER or "rules").strip().lower()
        self.model = LLM_MODEL
        self.graph = self._build_graph()
        self._gemini_client: Any | None = None
        self._gemini_types: Any | None = None
        self._gemini_cooldown_until_ts = 0.0
        if self.provider == "gemini":
            self._init_gemini_client()

    def evaluate(self, context: dict[str, Any]) -> LLMDecision:
        if self.provider == "gemini" and self._gemini_client is not None and time.time() >= self._gemini_cooldown_until_ts:
            try:
                decision = self._evaluate_with_gemini(context)
                self._gemini_cooldown_until_ts = 0.0
                return self._to_decision(decision, context)
            except Exception as exc:  # noqa: BLE001
                self._gemini_cooldown_until_ts = time.time() + 300.0
                logger.warning("Gemini evaluate failed, fallback for 300s: %s", exc)

        if self.graph is None:
            return self._fallback_decision(context)

        initial_state = {
            "context": context,
            "data": {},
            "analysis": {},
            "risk": {},
            "decision": {},
        }
        try:
            final_state = self.graph.invoke(initial_state)
            raw = final_state.get("decision", {}) if isinstance(final_state, dict) else {}
            decision = TradingDecision.model_validate(raw)
            decision = self._normalize_decision(decision, context)
            return self._to_decision(decision, context)
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            logger.exception("LangGraph evaluate failed, fallback to rules: %s", exc)
            return self._fallback_decision(context)

    def _init_gemini_client(self) -> None:
        if not LLM_API_KEY:
            logger.warning("LLM_PROVIDER=gemini but LLM_API_KEY missing; fallback to rules")
            return
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore

            http_options = types.HttpOptions(
                timeout=LLM_REQUEST_TIMEOUT_SECONDS,
                retry_options=types.HttpRetryOptions(
                    attempts=max(1, int(LLM_HTTP_RETRY_ATTEMPTS)),
                ),
            )
            self._gemini_client = genai.Client(api_key=LLM_API_KEY, http_options=http_options)
            self._gemini_types = types
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini SDK unavailable, fallback to rules: %s", exc)
            self._gemini_client = None
            self._gemini_types = None

    def _evaluate_with_gemini(self, context: dict[str, Any]) -> TradingDecision:
        if self._gemini_client is None or self._gemini_types is None:
            raise RuntimeError("Gemini client is not initialized")

        user_prompt = self._build_user_prompt(context)
        types = self._gemini_types

        cfg = types.GenerateContentConfig(
            system_instruction=GEMINI_SYSTEM_PROMPT,
            temperature=LLM_TEMPERATURE,
            top_p=LLM_TOP_P,
            top_k=LLM_TOP_K,
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            response_schema=TradingDecision.model_json_schema(),
        )
        response = self._gemini_client.models.generate_content(
            model=self.model,
            contents=user_prompt,
            config=cfg,
        )

        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if isinstance(parsed, TradingDecision):
                decision = parsed
            else:
                decision = TradingDecision.model_validate(parsed)
            return self._normalize_decision(decision, context)

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise RuntimeError("Gemini returned empty response text")
        data = json.loads(text)
        decision = TradingDecision.model_validate(data)
        return self._normalize_decision(decision, context)

    def _build_user_prompt(self, context: dict[str, Any]) -> str:
        current_time = context.get("current_time") or datetime.now(timezone.utc).isoformat()
        market_question = str(context.get("market_question", "unknown"))
        city = str(context.get("city", "unknown"))
        station_name = str(context.get("station_name", "unknown"))
        end_date = str(context.get("end_date", "unknown"))
        hours_to_resolve = context.get("hours_to_resolve", "unknown")
        favorite_bin = context.get("favorite_bin", "unknown")
        model_prob = float(context.get("model_prob", 0.0)) * 100.0
        all_probs = context.get("all_probs_dict", {})
        prices = context.get("prices_dict", {})
        bankroll = float(context.get("bankroll_usd", 0.0))
        open_positions = context.get("open_positions_summary", "unknown")
        trades = context.get("trade_history_summary", "unknown")
        win_rate = float(context.get("win_rate", 0.0))
        liquidity = float(context.get("liquidity", 0.0))
        edge = float(context.get("edge", 0.0))
        station_bias = float(context.get("station_bias", 0.0))

        return (
            f"Current time: {current_time}\n"
            f"Market: {market_question}\n"
            f"City / Station: {city} - {station_name}\n"
            f"Resolution date: {end_date} ({hours_to_resolve} hours left)\n\n"
            "Ensemble Data (station-specific):\n"
            f"{favorite_bin}: {model_prob:.2f}%\n"
            f"All probs: {json.dumps(all_probs, ensure_ascii=True)}\n\n"
            "Current Polymarket Prices:\n"
            f"{json.dumps(prices, ensure_ascii=True)}\n\n"
            f"Edge: {edge:.4f}\n"
            f"Bankroll: ${bankroll:.2f}\n"
            f"Open positions: {open_positions}\n"
            f"Last 10 trades: {trades}\n"
            f"Historical win rate this station: {win_rate:.2f}%\n"
            f"Station bias: {station_bias:.4f}\n"
            f"Liquidity this market: ${liquidity:.2f}\n\n"
            "Analyze conservatively and output only valid JSON."
        )

    def _build_graph(self) -> Any | None:
        if StateGraph is None:
            return None

        graph = StateGraph(dict)
        graph.add_node("data_agent", self._data_agent)
        graph.add_node("analyst_agent", self._analyst_agent)
        graph.add_node("risk_agent", self._risk_agent)
        graph.add_node("decision_agent", self._decision_agent)
        graph.add_node("reflection_agent", self._reflection_agent)

        graph.add_edge(START, "data_agent")
        graph.add_edge("data_agent", "analyst_agent")
        graph.add_edge("analyst_agent", "risk_agent")
        graph.add_edge("risk_agent", "decision_agent")
        graph.add_edge("decision_agent", "reflection_agent")
        graph.add_edge("reflection_agent", END)
        return graph.compile()

    def _data_agent(self, state: dict[str, Any]) -> dict[str, Any]:
        ctx = state.get("context", {})
        state["data"] = {
            "model_prob": float(ctx.get("model_prob", 0.0)),
            "market_price": float(ctx.get("market_price", 1.0)),
            "edge": float(ctx.get("edge", 0.0)),
            "favorite_bin": str(ctx.get("favorite_bin", "unknown")),
            "bankroll": float(ctx.get("bankroll_usd", 0.0)),
            "open_positions": int(ctx.get("open_positions", 0)),
            "near_resolution": bool(ctx.get("near_resolution", False)),
            "tail_prob": float(ctx.get("tail_prob", 1.0)),
        }
        return state

    def _analyst_agent(self, state: dict[str, Any]) -> dict[str, Any]:
        data = state.get("data", {})
        model_prob = float(data.get("model_prob", 0.0))
        edge = float(data.get("edge", 0.0))

        score = 0.60
        score += min(0.25, max(0.0, (model_prob - 0.70) * 1.5))
        score += min(0.15, max(0.0, (edge - EDGE_MIN) * 1.0))
        if bool(data.get("near_resolution", False)):
            score += 0.05
        state["analysis"] = {
            "confidence_score": min(0.99, score),
            "thesis": "Model-vs-market gap appears actionable",
        }
        return state

    def _risk_agent(self, state: dict[str, Any]) -> dict[str, Any]:
        data = state.get("data", {})
        analysis = state.get("analysis", {})

        confidence = float(analysis.get("confidence_score", 0.0))
        bankroll = float(data.get("bankroll", 0.0))
        open_positions = int(data.get("open_positions", 0))
        size = bankroll * 0.02 if bankroll > 0 else 10.0
        if open_positions >= 5:
            size *= 0.5
        state["risk"] = {
            "allowed": confidence >= MIN_LLM_CONFIDENCE and float(data.get("edge", 0.0)) >= EDGE_MIN,
            "size_usdc": max(1.0, float(size)),
            "insurance_pct": 0.18,
        }
        return state

    def _decision_agent(self, state: dict[str, Any]) -> dict[str, Any]:
        data = state.get("data", {})
        analysis = state.get("analysis", {})
        risk = state.get("risk", {})

        confidence = float(analysis.get("confidence_score", 0.0))
        allowed = bool(risk.get("allowed", False))
        tail_prob = float(data.get("tail_prob", 1.0))
        action = "SKIP"
        if allowed:
            action = "BUY_NO_SPREAD" if tail_prob <= NO_SPREAD_MAX_PROB else "BUY_YES"

        state["decision"] = {
            "action": action,
            "target_bin": str(data.get("favorite_bin", "unknown")),
            "size_usdc": float(risk.get("size_usdc", 0.0)) if action != "SKIP" else 0.0,
            "insurance_pct": float(risk.get("insurance_pct", 0.0)) if action != "SKIP" else 0.0,
            "confidence": confidence,
            "reason": "Graph decision",
            "risk_notes": "confidence/risk gate applied",
        }
        return state

    def _reflection_agent(self, state: dict[str, Any]) -> dict[str, Any]:
        data = state.get("data", {})
        raw_decision = state.get("decision", {})
        decision = TradingDecision.model_validate(raw_decision)

        if decision.action != "SKIP":
            if float(data.get("edge", 0.0)) < EDGE_MIN or decision.confidence < MIN_LLM_CONFIDENCE:
                decision = TradingDecision(
                    action="SKIP",
                    target_bin=decision.target_bin,
                    size_usdc=0.0,
                    insurance_pct=0.0,
                    confidence=min(decision.confidence, MIN_LLM_CONFIDENCE - 0.01),
                    reason="Reflection veto: insufficient edge/confidence",
                    risk_notes="strict safety override",
                )

        state["decision"] = decision.model_dump()
        return state

    def _fallback_decision(self, context: dict[str, Any]) -> LLMDecision:
        model_prob = float(context.get("model_prob", 0.0))
        edge = float(context.get("edge", 0.0))
        near_resolution = bool(context.get("near_resolution", False))

        confidence = 0.60
        confidence += min(0.25, max(0.0, (model_prob - 0.70) * 1.5))
        confidence += min(0.15, max(0.0, (edge - EDGE_MIN) * 1.0))
        if near_resolution:
            confidence += 0.05
        confidence = min(0.99, confidence)

        action = "BUY_NO_SPREAD" if (
            confidence >= MIN_LLM_CONFIDENCE and float(context.get("tail_prob", 1.0)) <= NO_SPREAD_MAX_PROB
        ) else "BUY_YES"
        if confidence < MIN_LLM_CONFIDENCE or edge < EDGE_MIN:
            action = "SKIP"

        output = TradingDecision(
            action=action,
            target_bin=str(context.get("favorite_bin", "unknown")),
            size_usdc=float(context.get("suggested_size_usdc", 0.0)) if action != "SKIP" else 0.0,
            insurance_pct=0.18 if action != "SKIP" else 0.0,
            confidence=confidence,
            reason="Fallback rules decision",
            risk_notes="non-LLM deterministic fallback",
        )
        output = self._normalize_decision(output, context)
        return self._to_decision(output, context)

    def _normalize_decision(self, output: TradingDecision, context: dict[str, Any]) -> TradingDecision:
        edge = float(context.get("edge", 0.0))
        if output.action != "SKIP" and (output.confidence < MIN_LLM_CONFIDENCE or edge < EDGE_MIN):
            return TradingDecision(
                action="SKIP",
                target_bin=output.target_bin,
                size_usdc=0.0,
                insurance_pct=0.0,
                confidence=min(output.confidence, MIN_LLM_CONFIDENCE - 0.01),
                reason="Safety override due to min confidence/edge",
                risk_notes=output.risk_notes,
            )
        if output.action == "SKIP":
            return TradingDecision(
                action="SKIP",
                target_bin=output.target_bin,
                size_usdc=0.0,
                insurance_pct=0.0,
                confidence=output.confidence,
                reason=output.reason,
                risk_notes=output.risk_notes,
            )
        return output

    def _to_decision(self, output: TradingDecision, context: dict[str, Any]) -> LLMDecision:
        is_buy = output.action in {"BUY_YES", "BUY_NO_SPREAD", "BUY_INSURANCE_ONLY", "BUY"}
        include_no_spread = output.action == "BUY_NO_SPREAD" or (
            bool(context.get("tail_prob", 1.0) <= NO_SPREAD_MAX_PROB) and is_buy
        )
        rationale = output.reason if not output.risk_notes else f"{output.reason} | {output.risk_notes}"
        return LLMDecision(
            action="BUY" if is_buy else "SKIP",
            confidence=float(output.confidence),
            rationale=rationale,
            include_no_spread=include_no_spread and is_buy,
            bin_label=str(output.target_bin or context.get("favorite_bin", "unknown")),
            size_usdc=float(output.size_usdc if is_buy else 0.0),
            insurance_pct=float(output.insurance_pct if is_buy else 0.0),
        )

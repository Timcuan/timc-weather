from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from config import (
    EDGE_MIN,
    INSURANCE_PCT,
    MAX_MARKET_PRICE,
    MIN_MODEL_PROB,
    NO_SPREAD_BIN_STEPS,
    NO_SPREAD_MAX_PROB,
    POLYMARKET_URL,
    TARGET_CITIES,
)
from polymarket_client import WeatherMarket


@dataclass
class EdgeResult:
    should_alert: bool
    market: WeatherMarket
    favorite_outcome: str
    favorite_model_prob: float
    favorite_market_price: float
    edge: float
    outcome_probs: dict[str, float]
    insurance_plan: list[dict[str, Any]]
    no_spread_targets: list[dict[str, Any]]


class EdgeCalculator:
    def detect_edge(
        self,
        market: WeatherMarket,
        weather_data: dict[str, Any],
        market_prices: dict[str, float],
    ) -> EdgeResult | None:
        outcome_probs = self._project_model_probs_to_outcomes(market.outcomes, weather_data["all_probs"])
        if not outcome_probs:
            return None

        favorite_outcome = max(outcome_probs, key=outcome_probs.get)
        favorite_model_prob = float(outcome_probs[favorite_outcome])
        favorite_market_price = float(market_prices.get(favorite_outcome, 1.0))
        edge = favorite_model_prob - favorite_market_price

        if favorite_model_prob < MIN_MODEL_PROB:
            return None
        if favorite_market_price > MAX_MARKET_PRICE:
            return None
        if edge < EDGE_MIN:
            return None

        insurance_plan = self._build_insurance_plan(
            favorite_bin=float(weather_data["favorite_bin"]),
            precision=float(weather_data.get("precision", 1.0)),
            outcomes=market.outcomes,
            market_prices=market_prices,
        )
        no_spread_targets = self._build_no_spread_targets(
            favorite_bin=float(weather_data["favorite_bin"]),
            precision=float(weather_data.get("precision", 1.0)),
            outcome_probs=outcome_probs,
            outcomes=market.outcomes,
            market_prices=market_prices,
        )

        return EdgeResult(
            should_alert=True,
            market=market,
            favorite_outcome=favorite_outcome,
            favorite_model_prob=favorite_model_prob,
            favorite_market_price=favorite_market_price,
            edge=edge,
            outcome_probs=outcome_probs,
            insurance_plan=insurance_plan,
            no_spread_targets=no_spread_targets,
        )

    def build_telegram_message(self, edge: EdgeResult, weather_data: dict[str, Any]) -> str:
        city = TARGET_CITIES[edge.market.city_key]
        link = f"{POLYMARKET_URL}/{edge.market.slug}"
        station_label = weather_data.get("station_name", city.airport_station)

        insurance_lines = []
        for item in edge.insurance_plan:
            insurance_lines.append(
                f"- {item['outcome']} @ {item['price']:.2f} (alokasi {item['allocation_pct'] * 100:.1f}%)"
            )

        insurance_text = "\n".join(insurance_lines) if insurance_lines else "- Tidak ada outer bin murah yang available"

        return (
            "🌡️ *Weather Edge Detected*\n\n"
            f"*City:* {city.display_name}\n"
            f"*Airport Basis:* {station_label}\n"
            f"*Date:* {edge.market.resolve_date}\n"
            f"*Market:* [{edge.market.question}]({link})\n"
            f"*Model Favorite:* {edge.favorite_outcome}\n"
            f"*Model Prob:* {edge.favorite_model_prob * 100:.2f}%\n"
            f"*Market Price:* {edge.favorite_market_price:.2f} (<= {MAX_MARKET_PRICE:.2f})\n"
            f"*Edge:* {edge.edge:.2f} (>= {EDGE_MIN:.2f})\n"
            f"*Ensemble Members Parsed:* {weather_data['total_members']}\n\n"
            "*Action (Thread Strategy):*\n"
            f"- BUY {edge.favorite_outcome} (core size {100 - INSURANCE_PCT * 100:.1f}%)\n"
            f"- Cheap insurance total {INSURANCE_PCT * 100:.1f}% di outer bins:\n"
            f"{insurance_text}\n"
            f"- No-spread targets: {len(edge.no_spread_targets)}\n"
        )

    def _project_model_probs_to_outcomes(
        self,
        outcomes: list[str],
        all_probs: dict[str, float],
    ) -> dict[str, float]:
        mapped: dict[str, float] = {}
        numeric_probs = {float(k): float(v) for k, v in all_probs.items()}

        for outcome in outcomes:
            bounds = self._parse_outcome_bounds(outcome)
            if bounds is None:
                continue
            low, high = bounds
            prob = 0.0
            for temp_bin, p in numeric_probs.items():
                if low is not None and temp_bin < low:
                    continue
                if high is not None and temp_bin > high:
                    continue
                prob += p
            mapped[outcome] = prob

        return mapped

    def _parse_outcome_bounds(self, label: str) -> tuple[float | None, float | None] | None:
        text = label.lower().replace("°", " ")
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]

        if "or below" in text or "and below" in text or "below" in text:
            return (None, nums[0]) if nums else None

        if "or above" in text or "and above" in text or "above" in text:
            return (nums[0], None) if nums else None

        if len(nums) == 1:
            return (nums[0], nums[0])

        if len(nums) >= 2:
            return (min(nums[0], nums[1]), max(nums[0], nums[1]))

        return None

    def _build_insurance_plan(
        self,
        favorite_bin: float,
        precision: float,
        outcomes: list[str],
        market_prices: dict[str, float],
    ) -> list[dict[str, Any]]:
        step = precision if precision > 0 else 1.0
        target_bins = {
            round(favorite_bin - (2 * step), 1),
            round(favorite_bin - step, 1),
            round(favorite_bin + step, 1),
            round(favorite_bin + (2 * step), 1),
        }

        candidates: list[tuple[str, float]] = []

        for outcome in outcomes:
            bounds = self._parse_outcome_bounds(outcome)
            if bounds is None:
                continue
            low, high = bounds
            matching = False
            for b in target_bins:
                if low is not None and b < low:
                    continue
                if high is not None and b > high:
                    continue
                matching = True
                break
            if not matching:
                continue

            price = market_prices.get(outcome)
            if price is None:
                continue
            candidates.append((outcome, float(price)))

        if not candidates:
            return []

        candidates.sort(key=lambda x: x[1])
        selected = candidates[:4]
        alloc = INSURANCE_PCT / len(selected)

        return [
            {
                "outcome": outcome,
                "price": price,
                "allocation_pct": alloc,
            }
            for outcome, price in selected
        ]

    def _build_no_spread_targets(
        self,
        favorite_bin: float,
        precision: float,
        outcome_probs: dict[str, float],
        outcomes: list[str],
        market_prices: dict[str, float],
    ) -> list[dict[str, Any]]:
        step = precision if precision > 0 else 1.0
        target_bins = [round(favorite_bin + (step * bin_step), 1) for bin_step in NO_SPREAD_BIN_STEPS]
        selected: list[dict[str, Any]] = []

        for target_bin in target_bins:
            for outcome in outcomes:
                bounds = self._parse_outcome_bounds(outcome)
                if bounds is None:
                    continue
                low, high = bounds
                if low is not None and target_bin < low:
                    continue
                if high is not None and target_bin > high:
                    continue

                prob = float(outcome_probs.get(outcome, 1.0))
                if prob >= NO_SPREAD_MAX_PROB:
                    continue
                yes_price = market_prices.get(outcome)
                selected.append(
                    {
                        "outcome": outcome,
                        "bin": target_bin,
                        "prob": prob,
                        "yes_price": yes_price,
                        "no_price_estimate": (1.0 - float(yes_price)) if yes_price is not None else None,
                    }
                )
                break

        # Deduplicate same outcome if both +2/+3 map to same range.
        dedup: dict[str, dict[str, Any]] = {}
        for item in selected:
            key = str(item["outcome"])
            if key not in dedup or float(item["prob"]) < float(dedup[key]["prob"]):
                dedup[key] = item
        return list(dedup.values())

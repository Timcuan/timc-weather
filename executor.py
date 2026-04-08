from __future__ import annotations

import logging
from typing import Any

from config import CLOB_BASE_URL, PAPER_TRADING, POLYMARKET_PROXY_ADDRESS, PRIVATE_KEY

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(self) -> None:
        self.paper = PAPER_TRADING
        self.live_ready = False
        self.client: Any | None = None
        self.order_type_fok: Any | None = None
        self.market_order_args_cls: Any | None = None
        self.buy_constant: Any | None = None
        self.sell_constant: Any | None = None
        self._init_live_client()

    def _init_live_client(self) -> None:
        if self.paper:
            return
        if not PRIVATE_KEY:
            logger.warning("Live mode requested but PRIVATE_KEY is missing; disabling live trading")
            return
        try:
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import MarketOrderArgs, OrderType  # type: ignore
            from py_clob_client.order_builder.constants import BUY, SELL  # type: ignore

            kwargs: dict[str, Any] = {
                "host": CLOB_BASE_URL,
                "key": PRIVATE_KEY,
                "chain_id": 137,
            }
            if POLYMARKET_PROXY_ADDRESS:
                kwargs["signature_type"] = 1
                kwargs["funder"] = POLYMARKET_PROXY_ADDRESS
            else:
                kwargs["signature_type"] = 0

            client = ClobClient(**kwargs)
            client.set_api_creds(client.create_or_derive_api_creds())

            self.client = client
            self.order_type_fok = OrderType.FOK
            self.market_order_args_cls = MarketOrderArgs
            self.buy_constant = BUY
            self.sell_constant = SELL
            self.live_ready = True
            logger.info("Live trading client initialized")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to initialize live trading client; keeping paper fallback: %s", exc)
            self.live_ready = False

    def execute(
        self,
        market: Any,
        edge: Any,
        market_prices: dict[str, float],
        position_size_usd: float,
        include_no_spread: bool,
    ) -> dict[str, Any]:
        plan = self._build_plan(market, edge, market_prices, position_size_usd, include_no_spread)

        if self.paper:
            logger.info("[PAPER] execution planned for %s: %s", market.market_id, plan)
            return {
                "status": "paper_executed",
                "paper": True,
                "orders": plan,
            }

        if not self.live_ready or not self.client:
            logger.warning("Live trading is not ready; skipping execution")
            return {
                "status": "live_unavailable",
                "paper": False,
                "orders": plan,
            }

        executions: list[dict[str, Any]] = []
        for order in plan:
            token_id = market.outcome_to_token_id.get(order.get("outcome"))
            amount_usd = float(order.get("amount_usd", 0.0))
            if not token_id or amount_usd <= 0:
                executions.append(
                    {
                        "outcome": order.get("outcome"),
                        "status": "invalid_order",
                    }
                )
                continue

            try:
                order_side = self.buy_constant if order.get("type") == "YES" else self.sell_constant
                market_order = self.market_order_args_cls(
                    token_id=token_id,
                    amount=amount_usd,
                    side=order_side,
                    order_type=self.order_type_fok,
                )
                signed = self.client.create_market_order(market_order)
                resp = self.client.post_order(signed, self.order_type_fok)
                executions.append(
                    {
                        "outcome": order.get("outcome"),
                        "status": "submitted",
                        "response": resp,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                executions.append(
                    {
                        "outcome": order.get("outcome"),
                        "status": "error",
                        "error": str(exc),
                    }
                )

        live_status = "live_executed"
        if any(item.get("status") == "error" for item in executions):
            live_status = "live_executed_with_errors"

        return {
            "status": live_status,
            "paper": False,
            "orders": plan,
            "executions": executions,
        }

    def _build_plan(
        self,
        market: Any,
        edge: Any,
        market_prices: dict[str, float],
        position_size_usd: float,
        include_no_spread: bool,
    ) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []

        core_alloc = 0.82
        core_amount = position_size_usd * core_alloc
        orders.append(
            {
                "side": "BUY",
                "type": "YES",
                "outcome": edge.favorite_outcome,
                "price": market_prices.get(edge.favorite_outcome),
                "amount_usd": round(core_amount, 2),
            }
        )

        insurance_alloc = position_size_usd - core_amount
        insurance_items = edge.insurance_plan
        if insurance_items:
            each = insurance_alloc / len(insurance_items)
            for item in insurance_items:
                orders.append(
                    {
                        "side": "BUY",
                        "type": "YES",
                        "outcome": item["outcome"],
                        "price": item["price"],
                        "amount_usd": round(each, 2),
                    }
                )

        if include_no_spread:
            targets = list(getattr(edge, "no_spread_targets", []) or [])
            if targets:
                no_alloc = min(position_size_usd * 0.10, 10.0)
                each_no = no_alloc / len(targets)
                for target in targets:
                    outcome = str(target["outcome"])
                    yes_price = market_prices.get(outcome)
                    no_price = target.get("no_price_estimate")
                    orders.append(
                        {
                            "side": "BUY",
                            "type": "NO",
                            "outcome": outcome,
                            "price": no_price,
                            "amount_usd": round(each_no, 2),
                        }
                    )

        return orders

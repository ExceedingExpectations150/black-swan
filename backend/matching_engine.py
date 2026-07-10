"""Continuous Double Auction matching engine for ChaosNet "Black Swan".

Pure order-matching logic (PRD.md Phase 3): no database access, no I/O.
Operates on OrderBook rows built by the tick engine and mutates only their
`status` field (PENDING -> FILLED when fully matched).
"""

from __future__ import annotations

from dataclasses import dataclass

from models import OrderBook, OrderStatus, OrderType


@dataclass(frozen=True)
class ClearedTransaction:
    """One matched buy/sell pair executed at the pair's midpoint price."""

    buy_order_id: str
    sell_order_id: str
    buyer_agent_id: str
    seller_agent_id: str
    quantity: int
    price: float


class MatchingEngine:
    """Continuous Double Auction: match crossing limit orders per tick."""

    def resolve_order_book(
        self, orders: list[OrderBook], baseline_price: float
    ) -> tuple[float, list[ClearedTransaction], int]:
        """Match crossing orders and derive the tick's clearing price.

        BUY orders are sorted by limit price descending, SELL orders
        ascending. While the best buy limit >= best sell limit, the pair
        trades min(remaining quantities) at the midpoint of the two limits.
        The clearing price is the VOLUME-WEIGHTED average of the matched
        pair prices, so a large fill moves the print more than a 1-share
        fill; with no matches it stays at `baseline_price`.

        Returns (clearing_price, cleared_transactions, total_volume).
        """
        buys: list[OrderBook] = sorted(
            (o for o in orders if o.order_type == OrderType.BUY),
            key=lambda o: o.limit_price,
            reverse=True,
        )
        sells: list[OrderBook] = sorted(
            (o for o in orders if o.order_type == OrderType.SELL),
            key=lambda o: o.limit_price,
        )

        remaining: dict[str, int] = {o.order_id: o.quantity for o in orders}
        transactions: list[ClearedTransaction] = []

        buy_idx = 0
        sell_idx = 0
        while buy_idx < len(buys) and sell_idx < len(sells):
            buy = buys[buy_idx]
            sell = sells[sell_idx]
            if buy.limit_price < sell.limit_price:
                break  # book no longer crosses

            quantity = min(remaining[buy.order_id], remaining[sell.order_id])
            pair_price = (buy.limit_price + sell.limit_price) / 2.0
            transactions.append(
                ClearedTransaction(
                    buy_order_id=buy.order_id,
                    sell_order_id=sell.order_id,
                    buyer_agent_id=buy.agent_id,
                    seller_agent_id=sell.agent_id,
                    quantity=quantity,
                    price=pair_price,
                )
            )

            remaining[buy.order_id] -= quantity
            remaining[sell.order_id] -= quantity
            if remaining[buy.order_id] == 0:
                buy.status = OrderStatus.FILLED
                buy_idx += 1
            if remaining[sell.order_id] == 0:
                sell.status = OrderStatus.FILLED
                sell_idx += 1

        total_volume: int = sum(t.quantity for t in transactions)
        if total_volume > 0:
            clearing_price: float = (
                sum(t.price * t.quantity for t in transactions) / total_volume
            )
        else:
            clearing_price = baseline_price

        return clearing_price, transactions, total_volume

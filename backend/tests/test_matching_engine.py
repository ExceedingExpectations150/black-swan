"""Unit tests for the Continuous Double Auction matching engine.

Run: python tests/test_matching_engine.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matching_engine import MatchingEngine
from models import OrderBook, OrderStatus, OrderType


def order(order_id: str, agent: str, side: OrderType, qty: int, limit: float) -> OrderBook:
    return OrderBook(
        order_id=order_id,
        tick_id=1,
        agent_id=agent,
        order_type=side,
        quantity=qty,
        limit_price=limit,
        status=OrderStatus.PENDING,
    )


def main() -> None:
    engine = MatchingEngine()

    # 1. Empty book: clearing price falls back to baseline.
    price, txs, vol = engine.resolve_order_book([], baseline_price=100.0)
    assert (price, txs, vol) == (100.0, [], 0)

    # 2. Non-crossing book: buy 98 < sell 102 -> no trades.
    price, txs, vol = engine.resolve_order_book(
        [
            order("b1", "A", OrderType.BUY, 10, 98.0),
            order("s1", "B", OrderType.SELL, 10, 102.0),
        ],
        baseline_price=100.0,
    )
    assert (price, len(txs), vol) == (100.0, 0, 0)

    # 3. Simple cross: buy 102 vs sell 98 -> 10 shares at midpoint 100.
    b = order("b1", "A", OrderType.BUY, 10, 102.0)
    s = order("s1", "B", OrderType.SELL, 10, 98.0)
    price, txs, vol = engine.resolve_order_book([b, s], baseline_price=90.0)
    assert price == 100.0 and vol == 10 and len(txs) == 1
    assert txs[0].buyer_agent_id == "A" and txs[0].seller_agent_id == "B"
    assert b.status == OrderStatus.FILLED and s.status == OrderStatus.FILLED

    # 4. Price-priority + partial fill: best buy (105) eats the best sell (95)
    #    first at 100.0, remainder matches the next sell (99) at 102.0.
    b1 = order("b1", "A", OrderType.BUY, 15, 105.0)
    b2 = order("b2", "B", OrderType.BUY, 5, 96.0)
    s1 = order("s1", "C", OrderType.SELL, 10, 95.0)
    s2 = order("s2", "D", OrderType.SELL, 20, 99.0)
    price, txs, vol = engine.resolve_order_book([b1, b2, s1, s2], baseline_price=100.0)
    assert [(t.quantity, t.price) for t in txs] == [(10, 100.0), (5, 102.0)]
    assert vol == 15
    assert price == (100.0 + 102.0) / 2
    assert b1.status == OrderStatus.FILLED  # fully consumed across two pairs
    assert s1.status == OrderStatus.FILLED
    assert s2.status == OrderStatus.PENDING  # 15 of 20 left unfilled
    assert b2.status == OrderStatus.PENDING  # 96 < 99: never crossed

    print("PASS: empty book, non-crossing, simple cross, priority + partial fills")


if __name__ == "__main__":
    main()

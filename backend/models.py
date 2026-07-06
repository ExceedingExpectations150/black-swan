"""SQLAlchemy models for the ChaosNet "Black Swan" market twin.

Implements the exact relational schema from PRD.md Section 3:
`world_states`, `agent_states`, and `order_books`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base shared by all ChaosNet models."""


class AgentType(str, enum.Enum):
    GEMMA_RETAIL_COHORT = "gemma_retail_cohort"
    TIMESFM_INSTITUTIONAL = "timesfm_institutional"


class OrderType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class WorldState(Base):
    """One market tick: authoritative clearing price plus macro-shock context."""

    __tablename__ = "world_states"

    tick_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    news_headline: Mapped[str] = mapped_column(String, nullable=False, default="")
    system_stress_index: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    orders: Mapped[list["OrderBook"]] = relationship(back_populates="world_state")

    def __repr__(self) -> str:
        return (
            f"<WorldState tick={self.tick_id} price={self.current_price:.2f} "
            f"stress={self.system_stress_index:.3f}>"
        )


class AgentState(Base):
    """A single market participant: Gemma behavioral cohort or TimesFM quant."""

    __tablename__ = "agent_states"

    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    agent_type: Mapped[AgentType] = mapped_column(
        Enum(AgentType, values_callable=lambda e: [m.value for m in e], native_enum=False),
        nullable=False,
    )
    cash_balance: Mapped[float] = mapped_column(Float, nullable=False, default=100_000.0)
    stock_inventory: Mapped[int] = mapped_column(Integer, nullable=False, default=1_000)
    risk_tolerance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    is_bankrupt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    orders: Mapped[list["OrderBook"]] = relationship(back_populates="agent")

    def __repr__(self) -> str:
        return (
            f"<AgentState id={self.agent_id[:8]} type={self.agent_type.value} "
            f"cash={self.cash_balance:.2f} inventory={self.stock_inventory}>"
        )


class OrderBook(Base):
    """A single limit order submitted into the Continuous Double Auction."""

    __tablename__ = "order_books"

    order_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    tick_id: Mapped[int] = mapped_column(Integer, ForeignKey("world_states.tick_id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_states.agent_id"), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(
        Enum(OrderType, values_callable=lambda e: [m.value for m in e], native_enum=False),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    limit_price: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, values_callable=lambda e: [m.value for m in e], native_enum=False),
        nullable=False,
        default=OrderStatus.PENDING,
    )

    world_state: Mapped[WorldState] = relationship(back_populates="orders")
    agent: Mapped[AgentState] = relationship(back_populates="orders")

    def __repr__(self) -> str:
        return (
            f"<OrderBook id={self.order_id[:8]} {self.order_type.value} "
            f"{self.quantity}@{self.limit_price:.2f} status={self.status.value}>"
        )

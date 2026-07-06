"""SQLAlchemy models for the ChaosNet "Black Swan" market twin.

Implements the exact relational schema from PRD.md Section 3:
`world_states`, `agent_states`, and `order_books`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
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
    risk_tolerance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    is_bankrupt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    orders: Mapped[list["OrderBook"]] = relationship(back_populates="agent")
    holdings: Mapped[list["AgentHolding"]] = relationship(back_populates="agent")

    def __repr__(self) -> str:
        return (
            f"<AgentState id={self.agent_id[:8]} type={self.agent_type.value} "
            f"cash={self.cash_balance:.2f}>"
        )


class OrderBook(Base):
    """A single limit order submitted into the Continuous Double Auction."""

    __tablename__ = "order_books"

    order_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    tick_id: Mapped[int] = mapped_column(Integer, ForeignKey("world_states.tick_id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_states.agent_id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), ForeignKey("companies.ticker"), nullable=False)
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


class AuthorType(str, enum.Enum):
    COMPANY = "company"
    ANALYST = "analyst"
    TRADER = "trader"


class Company(Base):
    """A real-world company anchored to a real market price at seed time."""

    __tablename__ = "companies"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sector: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String, nullable=False)
    city: Mapped[str] = mapped_column(String, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    shares_outstanding: Mapped[int] = mapped_column(BigInteger, nullable=False)
    anchor_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    sentiment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    volatility: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_bankrupt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<Company {self.ticker} price={self.current_price:.2f} sent={self.sentiment:+.2f}>"


class AgentHolding(Base):
    """Per-ticker share position for one agent (replaces scalar inventory)."""

    __tablename__ = "agent_holdings"

    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_states.agent_id"), primary_key=True
    )
    ticker: Mapped[str] = mapped_column(
        String(16), ForeignKey("companies.ticker"), primary_key=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    agent: Mapped[AgentState] = relationship(back_populates="holdings")


class PriceTick(Base):
    """Per-ticker clearing price history (TimesFM context source)."""

    __tablename__ = "price_ticks"
    __table_args__ = (Index("ix_price_ticks_ticker_tick", "ticker", "tick_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), ForeignKey("companies.ticker"), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class SocialPost(Base):
    """One post on the simulated social feed."""

    __tablename__ = "social_posts"

    post_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    tick_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    author_type: Mapped[AuthorType] = mapped_column(
        Enum(AuthorType, values_callable=lambda e: [m.value for m in e], native_enum=False),
        nullable=False,
    )
    author_ticker: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("companies.ticker"), nullable=True
    )
    author_display: Mapped[str] = mapped_column(String, nullable=False)
    handle: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reposts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EconomySnapshot(Base):
    """Per-tick quantitative economy rollup plus periodic macro narrative."""

    __tablename__ = "economy_snapshots"

    tick_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    system_stress_index: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    bankrupt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sectors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    movers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    narrative: Mapped[str] = mapped_column(Text, nullable=False, default="")

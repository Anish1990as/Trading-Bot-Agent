from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )
    risk_percentage: Mapped[float] = mapped_column(Float, default=1.0)
    total_capital: Mapped[float] = mapped_column(Float, default=100000.0)
    daily_loss_limit: Mapped[float] = mapped_column(Float, default=5000.0)
    daily_profit_target: Mapped[float] = mapped_column(Float, default=10000.0)
    telegram_bot_token: Mapped[str | None] = mapped_column(String, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String, nullable=True)
    paper_trading: Mapped[bool] = mapped_column(Boolean, default=True)


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    exchange: Mapped[str] = mapped_column(String, default="NSE")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    symbol: Mapped[str] = mapped_column(String, index=True)
    signal_type: Mapped[str] = mapped_column(String)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    target_1: Mapped[float] = mapped_column(Float)
    target_2: Mapped[float] = mapped_column(Float)
    target_3: Mapped[float] = mapped_column(Float)
    confidence_score: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="PENDING")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    trade_type: Mapped[str] = mapped_column(String)
    entry_price: Mapped[float] = mapped_column(Float)

    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    highest_target_hit: Mapped[int] = mapped_column(Integer, default=0)

    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    profit_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_used: Mapped[str] = mapped_column(String)
    entry_time: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

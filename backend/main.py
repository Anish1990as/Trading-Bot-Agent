import os
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .database import engine, Base, get_db
from . import models
from .execution_engine.paper_trading import PaperExecutionEngine
from .market_data.fetcher import MarketDataFetcher
from .options_helper import get_live_nse_option_ltp
from .trade_metadata import (
    display_symbol,
    is_option_execution,
    parse_trade_metadata,
    trade_direction,
)

# Create all tables in the database
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Algorithmic Trading Platform API")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
market_fetcher = MarketDataFetcher(data_dir=DATA_DIR)


class CloseTradeRequest(BaseModel):
    exit_price: Optional[float] = None
    reason: str = "Manual Close"


def _get_latest_trade_price(symbol: str) -> float:
    df = market_fetcher.fetch_live_data(symbol, "NSE", "1m")
    if df is None or df.empty:
        raise HTTPException(status_code=503, detail=f"Unable to fetch live price for {symbol}")
    return float(df.iloc[-1]["close"])


def _get_trade_exit_price(trade: models.Trade) -> float:
    metadata = parse_trade_metadata(trade.notes)
    if is_option_execution(metadata):
        option_ltp = get_live_nse_option_ltp(
            trade.symbol,
            metadata["opt_type"],
            metadata["strike"],
            metadata.get("expiry"),
        )
        if option_ltp is None or option_ltp <= 0:
            raise HTTPException(
                status_code=503,
                detail=f"Unable to fetch live option price for {display_symbol(trade.symbol, metadata)}",
            )
        return float(option_ltp)

    return _get_latest_trade_price(trade.symbol)


def _serialize_trade_with_live_metrics(trade: models.Trade) -> dict:
    payload = {column.name: getattr(trade, column.name) for column in trade.__table__.columns}
    metadata = parse_trade_metadata(trade.notes)
    payload["current_price"] = None
    payload["live_profit_loss"] = None
    payload["display_symbol"] = display_symbol(trade.symbol, metadata)
    payload["signal_direction"] = trade_direction(trade.trade_type, metadata)
    payload["execution_instrument"] = metadata.get("execution_instrument", "UNDERLYING")
    payload["close_reason"] = metadata.get("close_reason")
    payload["option_type"] = None
    payload["strike"] = None

    if is_option_execution(metadata):
        payload["option_symbol"] = payload["display_symbol"]
        payload["option_type"] = metadata["opt_type"]
        payload["strike"] = metadata["strike"]

    if trade.status != "OPEN":
        return payload

    try:
        current_price = _get_trade_exit_price(trade)
    except HTTPException:
        return payload

    payload["current_price"] = current_price

    pnl_per_unit = current_price - trade.entry_price if trade.trade_type == "BUY" else trade.entry_price - current_price
    payload["live_profit_loss"] = pnl_per_unit * trade.quantity

    return payload

# Setup CORS for Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Welcome to the Algorithmic Trading Platform Local Server"}

@app.get("/api/health")
def health_check():
    return {"status": "healthy"}

@app.get("/api/watchlist")
def get_watchlist(db: Session = Depends(get_db)):
    return db.query(models.Watchlist).all()

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    trades = db.query(models.Trade).all()
    winning = [t for t in trades if t.profit_loss and t.profit_loss > 0]
    total = len(trades)
    closed = len([t for t in trades if t.status == "CLOSED"])
    
    net_pnl = sum(t.profit_loss for t in trades if t.profit_loss)
    win_rate = (len(winning) / closed * 100) if closed > 0 else 0
    
    return {
        "total_trades": total,
        "closed_trades": closed,
        "win_rate": round(win_rate, 2),
        "net_pnl": round(net_pnl, 2),
        "open_trades": total - closed
    }

@app.get("/api/trades/recent")
def get_recent_trades(db: Session = Depends(get_db)):
    trades = db.query(models.Trade).order_by(models.Trade.entry_time.desc()).limit(20).all()
    return [_serialize_trade_with_live_metrics(trade) for trade in trades]


@app.get("/api/trades/open")
def get_open_trades(db: Session = Depends(get_db)):
    trades = (
        db.query(models.Trade)
        .filter(models.Trade.status == "OPEN")
        .order_by(models.Trade.entry_time.desc())
        .all()
    )
    return [_serialize_trade_with_live_metrics(trade) for trade in trades]


@app.post("/api/trades/{trade_id}/close")
def close_trade(trade_id: int, payload: CloseTradeRequest, db: Session = Depends(get_db)):
    trade = db.query(models.Trade).filter(models.Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status == "CLOSED":
        raise HTTPException(status_code=409, detail="Trade is already closed")

    exit_price = payload.exit_price if payload.exit_price is not None else _get_trade_exit_price(trade)
    execution_engine = PaperExecutionEngine(db_session=db)
    closed_trade = execution_engine.close_position(trade_id, exit_price, payload.reason)

    if not closed_trade:
        raise HTTPException(status_code=500, detail="Trade could not be closed")

    return {
        "message": "Trade closed successfully",
        "trade": closed_trade,
        "exit_price": exit_price,
        "reason": payload.reason
    }

@app.get("/api/signals/recent")
def get_recent_signals(db: Session = Depends(get_db)):
    return db.query(models.Signal).order_by(models.Signal.timestamp.desc()).limit(20).all()

@app.get("/api/backtest/run/{symbol}/{timeframe}")
def run_backtest(symbol: str, timeframe: str):
    from .backtest_engine.backtester import BacktestEngine
    import os
    
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
    filepath = os.path.join(data_dir, f"{symbol}_{timeframe}.parquet")
    
    bt = BacktestEngine(data_file=filepath)
    report = bt.run()
    
    return report

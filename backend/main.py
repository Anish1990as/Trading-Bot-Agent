import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

# Ensure .env is loaded from the project root
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(_env_path):
    load_dotenv(_env_path, override=True)
else:
    load_dotenv(override=True)

import requests
import pytz
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import engine, Base, get_db
from . import models
from .execution_engine.dhan_execution import DhanExecutionEngine
from .execution_engine.paper_trading import PaperExecutionEngine
from .market_data.fetcher import MarketDataFetcher
from .strategy_engine.ai_signal_engine import AISignalEngine
from .options_helper import get_dhan_option_ltp, get_dhan_option_security_id
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
ai_engine = AISignalEngine()


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
        segment = "BSE_FNO" if trade.symbol.upper() == "SENSEX" else "NSE_FNO"
        security_id = metadata.get("security_id")
        if not security_id:
            security_id = get_dhan_option_security_id(
                trade.symbol,
                metadata.get("opt_type", "CE"),
                int(metadata.get("strike", 0)),
                metadata.get("expiry", ""),
            )
        if security_id:
            option_ltp = get_dhan_option_ltp(security_id, segment)
            if option_ltp is not None and option_ltp > 0:
                return float(option_ltp)

        return float(trade.entry_price)

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

@app.get("/api/broker/status")
def get_broker_status(db: Session = Depends(get_db)):
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=True)
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    paper_trading = os.getenv("PAPER_TRADING", "true").lower() != "false"
    live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

    is_configured = bool(
        client_id 
        and access_token 
        and client_id.upper() not in {"DUMMY", "YOUR_CLIENT_ID", "REPLACE_ME"}
    )
    
    masked_client_id = f"***{client_id[-4:]}" if client_id and len(client_id) > 4 else "Not Configured"
    
    broker_info = {
        "status": "DISCONNECTED",
        "client_id": masked_client_id,
        "is_configured": is_configured,
        "mode": "PAPER" if paper_trading else ("LIVE" if live_enabled else "STANDBY"),
        "available_balance": 0.0,
        "sod_limit": 0.0,
        "utilized_amount": 0.0,
        "collateral_amount": 0.0,
        "open_broker_positions": 0,
        "last_synced": datetime.now(timezone.utc).isoformat(),
    }

    if not is_configured:
        return broker_info

    try:
        # Check fund limits
        res = requests.get(
            "https://api.dhan.co/v2/fundlimit",
            headers={
                "access-token": access_token or "",
                "client-id": client_id or "",
                "Accept": "application/json",
            },
            timeout=(2.0, 4.0),
        )
        if res.status_code == 200:
            data = res.json()
            broker_info["status"] = "CONNECTED"
            broker_info["available_balance"] = float(data.get("availabelBalance") or 0.0)
            broker_info["sod_limit"] = float(data.get("sodLimit") or 0.0)
            broker_info["utilized_amount"] = float(data.get("utilizedAmount") or 0.0)
            broker_info["collateral_amount"] = float(data.get("collateralAmount") or 0.0)
    except Exception:
        broker_info["status"] = "ERROR"

    return broker_info

import pandas as pd

@app.get("/api/market/overview")
def get_market_overview():
    symbols = ["NIFTY", "SENSEX", "BANKNIFTY"]
    overview = []
    
    for sym in symbols:
        try:
            exchange = "BSE" if sym == "SENSEX" else "NSE"
            df = market_fetcher.fetch_live_data(sym, exchange, "1m")
            if df is None or df.empty:
                for tf in ["1m", "5m", "15m"]:
                    p_path = os.path.join(DATA_DIR, f"{sym}_{tf}.parquet")
                    if os.path.exists(p_path):
                        try:
                            df = pd.read_parquet(p_path)
                            if df is not None and not df.empty:
                                break
                        except Exception:
                            pass
            if df is not None and not df.empty:
                # Isolate today's session if available for true Day Change and Day High/Low
                try:
                    df['dt_parsed'] = pd.to_datetime(df['datetime'])
                    today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                    today_df = df[df['dt_parsed'].dt.strftime("%Y-%m-%d") == today_str]
                    if not today_df.empty:
                        last_price = float(today_df.iloc[-1]["close"])
                        open_price = float(today_df.iloc[0]["open"])
                        high_price = float(today_df["high"].max())
                        low_price = float(today_df["low"].min())
                    else:
                        last_price = float(df.iloc[-1]["close"])
                        open_price = float(df.iloc[0]["open"])
                        high_price = float(df["high"].max())
                        low_price = float(df["low"].min())
                except Exception:
                    last_price = float(df.iloc[-1]["close"])
                    open_price = float(df.iloc[0]["open"]) if len(df) > 0 else last_price
                    high_price = float(df["high"].max())
                    low_price = float(df["low"].min())

                change = last_price - open_price
                pct_change = (change / open_price * 100) if open_price > 0 else 0.0

                # Evaluate real-time market regime (Bullish, Bearish, Sideways)
                trend = "SIDEWAYS"
                regime = "SIDEWAYS / CHOPPY"
                regime_desc = "Consolidating. Waiting for breakout."
                confidence = 0
                buy_votes = 0
                sell_votes = 0
                
                try:
                    df_5m = market_fetcher.fetch_live_data(sym, exchange, "5m")
                    eval_target_df = df_5m if (df_5m is not None and not df_5m.empty) else df
                    if eval_target_df is not None and not eval_target_df.empty:
                        eval_res = ai_engine.multi_engine.evaluate_all(eval_target_df)
                        master = eval_res.get("master_trend", "NEUTRAL")
                        buy_votes = eval_res.get("buy_votes", 0)
                        sell_votes = eval_res.get("sell_votes", 0)
                        confidence = eval_res.get("base_confidence", 0)
                        
                        if master == "BULLISH" and buy_votes >= 1:
                            trend = "BULLISH"
                            regime = "STRONG BULLISH" if buy_votes >= 2 else "MILD BULLISH"
                            regime_desc = "Bullish momentum active. Looking for CE entries."
                        elif master == "BEARISH" and sell_votes >= 1:
                            trend = "BEARISH"
                            regime = "STRONG BEARISH" if sell_votes >= 2 else "MILD BEARISH"
                            regime_desc = "Bearish pressure active. Looking for PE entries."
                        elif master == "BULLISH":
                            trend = "SIDEWAYS"
                            regime = "SIDEWAYS (BULL BIAS)"
                            regime_desc = "Consolidating with bullish bias. Waiting for breakout."
                        elif master == "BEARISH":
                            trend = "SIDEWAYS"
                            regime = "SIDEWAYS (BEAR BIAS)"
                            regime_desc = "Consolidating with bearish bias. Rangebound chop."
                        else:
                            trend = "SIDEWAYS"
                            regime = "SIDEWAYS / FLAT"
                            regime_desc = "Market is flat/rangebound. Bot waiting for clear trend."
                except Exception:
                    pass

                overview.append({
                    "symbol": sym,
                    "last_price": round(last_price, 2),
                    "change": round(change, 2),
                    "pct_change": round(pct_change, 2),
                    "high": round(high_price, 2),
                    "low": round(low_price, 2),
                    "trend": trend,
                    "regime": regime,
                    "regime_desc": regime_desc,
                    "confidence": confidence,
                    "buy_votes": buy_votes,
                    "sell_votes": sell_votes,
                })
        except Exception:
            continue
            
    return overview

@app.get("/api/watchlist")
def get_watchlist(db: Session = Depends(get_db)):
    return db.query(models.Watchlist).all()

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    trades = db.query(models.Trade).all()
    winning = [t for t in trades if t.profit_loss and t.profit_loss > 0]
    losing = [t for t in trades if t.profit_loss and t.profit_loss < 0]
    total = len(trades)
    closed = len([t for t in trades if t.status == "CLOSED"])
    
    net_pnl = sum(t.profit_loss for t in trades if t.profit_loss)
    win_rate = (len(winning) / closed * 100) if closed > 0 else 0
    
    total_profit = sum(t.profit_loss for t in winning)
    total_loss = abs(sum(t.profit_loss for t in losing))
    profit_factor = round(total_profit / total_loss, 2) if total_loss > 0 else (round(total_profit, 2) if total_profit > 0 else 1.0)
    
    return {
        "total_trades": total,
        "closed_trades": closed,
        "win_rate": round(win_rate, 2),
        "net_pnl": round(net_pnl, 2),
        "open_trades": total - closed,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "profit_factor": profit_factor,
    }

@app.get("/api/analytics/pnl-history")
def get_pnl_history(db: Session = Depends(get_db)):
    trades = (
        db.query(models.Trade)
        .filter(models.Trade.status == "CLOSED")
        .order_by(models.Trade.entry_time.asc())
        .all()
    )
    
    cumulative_points = []
    current_pnl = 0.0
    strategy_map = {}
    
    for idx, trade in enumerate(trades):
        pnl = float(trade.profit_loss or 0.0)
        current_pnl += pnl
        entry_time_str = trade.entry_time.isoformat() if trade.entry_time else f"T-{idx+1}"
        cumulative_points.append({
            "trade_id": trade.id,
            "trade_num": idx + 1,
            "symbol": trade.symbol,
            "pnl": round(pnl, 2),
            "cumulative_pnl": round(current_pnl, 2),
            "time": entry_time_str,
            "strategy": trade.strategy_used or "General",
        })
        
        strat = trade.strategy_used or "Manual / Other"
        if strat not in strategy_map:
            strategy_map[strat] = {"trades": 0, "wins": 0, "pnl": 0.0}
        strategy_map[strat]["trades"] += 1
        if pnl > 0:
            strategy_map[strat]["wins"] += 1
        strategy_map[strat]["pnl"] = round(strategy_map[strat]["pnl"] + pnl, 2)

    strategy_breakdown = [
        {
            "strategy": strat,
            "trades": stats["trades"],
            "wins": stats["wins"],
            "win_rate": round((stats["wins"] / stats["trades"] * 100), 1) if stats["trades"] > 0 else 0,
            "pnl": stats["pnl"],
        }
        for strat, stats in strategy_map.items()
    ]

    return {
        "cumulative_history": cumulative_points,
        "strategy_breakdown": strategy_breakdown,
        "total_closed": len(trades),
        "final_pnl": round(current_pnl, 2),
    }

@app.get("/api/trades/recent")
def get_recent_trades(db: Session = Depends(get_db)):
    trades = db.query(models.Trade).order_by(models.Trade.entry_time.desc()).limit(30).all()
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

    settings = db.query(models.Settings).first()
    paper_trading = os.getenv(
        "PAPER_TRADING", str(settings is None or settings.paper_trading)
    ).lower() != "false"
    execution_engine = (
        PaperExecutionEngine(db_session=db)
        if paper_trading
        else DhanExecutionEngine(db_session=db)
    )
    exit_price = payload.exit_price
    if exit_price is None and paper_trading:
        exit_price = _get_trade_exit_price(trade)
    closed_trade = execution_engine.close_position(trade_id, exit_price, payload.reason)

    if not closed_trade:
        raise HTTPException(status_code=500, detail="Trade could not be closed")

    return {
        "message": "Trade closed successfully",
        "trade": closed_trade,
        "exit_price": exit_price,
        "reason": payload.reason
    }


@app.post("/api/trades/close-all")
def close_all_open_trades(payload: CloseTradeRequest, db: Session = Depends(get_db)):
    """Emergency Kill Switch to square off all active positions."""
    open_trades = db.query(models.Trade).filter(models.Trade.status == "OPEN").all()
    if not open_trades:
        return {"message": "No open trades to close", "closed_count": 0}

    settings = db.query(models.Settings).first()
    paper_trading = os.getenv(
        "PAPER_TRADING", str(settings is None or settings.paper_trading)
    ).lower() != "false"
    execution_engine = (
        PaperExecutionEngine(db_session=db)
        if paper_trading
        else DhanExecutionEngine(db_session=db)
    )

    closed_results = []
    for trade in open_trades:
        try:
            exit_price = _get_trade_exit_price(trade)
            closed = execution_engine.close_position(
                trade.id, exit_price, payload.reason or "Emergency Kill Switch"
            )
            if closed:
                closed_results.append(trade.id)
        except Exception:
            continue

    return {
        "message": f"Successfully closed {len(closed_results)} trades",
        "closed_count": len(closed_results),
        "closed_ids": closed_results,
    }


@app.get("/api/signals/recent")
def get_recent_signals(db: Session = Depends(get_db)):
    signals = db.query(models.Signal).order_by(models.Signal.timestamp.desc()).limit(15).all()
    return signals


@app.get("/api/backtest/run/{symbol}/{timeframe}")
def run_backtest(symbol: str, timeframe: str):
    from .backtest_engine.backtester import BacktestEngine
    import os
    
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
    filepath = os.path.join(data_dir, f"{symbol}_{timeframe}.parquet")
    
    bt = BacktestEngine(data_file=filepath)
    report = bt.run()
    
    return report

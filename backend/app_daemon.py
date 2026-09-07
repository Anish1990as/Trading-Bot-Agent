import os
import sys
import time
import traceback
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import SessionLocal, engine
from backend.execution_engine.paper_trading import PaperExecutionEngine
from backend.market_data.fetcher import MarketDataFetcher
from backend.models import Base, Settings, Signal, Trade, Watchlist
from backend.notifications.telegram_bot import TelegramNotifier
from backend.options_helper import build_options_info, get_live_nse_option_ltp
from backend.risk_management.risk_manager import RiskManager
from backend.strategy_engine.ai_signal_engine import AISignalEngine
from backend.trade_metadata import (
    display_symbol,
    is_option_execution,
    parse_trade_metadata,
    serialize_trade_metadata,
    trade_direction,
)


class TradingDaemon:
    def __init__(self):
        Base.metadata.create_all(bind=engine)
        self.db: Session = SessionLocal()
        load_dotenv()

        settings = self.db.query(Settings).first()
        if settings is None:
            settings = Settings()
            self.db.add(settings)
            self.db.commit()
            self.db.refresh(settings)
        self.settings: Settings = settings

        bot_token = self.settings.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = self.settings.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")

        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        self.timeframes = self._env_list("TRADE_TIMEFRAMES", ["5m", "15m"])
        self.symbol_filter = {
            symbol.upper() for symbol in self._env_list("TRADE_SYMBOLS", [])
        }
        self.min_trade_confidence = int(
            os.getenv(
                "MIN_TRADE_CONFIDENCE",
                os.getenv("TRADE_ALERT_MIN_CONFIDENCE", "75"),
            )
        )
        self.max_daily_trades = int(os.getenv("MAX_DAILY_TRADES", "0"))
        self.loss_cooldown_minutes = int(os.getenv("LOSS_COOLDOWN_MINUTES", "0"))
        self.trade_block_override_symbols = {
            self._symbol_key(symbol)
            for symbol in self._env_list(
                "TRADE_BLOCK_OVERRIDE_SYMBOLS",
                ["NIFTY", "NIFTY 50"],
            )
        }
        self.fetcher = MarketDataFetcher(data_dir=data_dir)
        self.ai_engine = AISignalEngine()
        self.risk_manager = RiskManager(
            total_capital=self.settings.total_capital,
            risk_per_trade_pct=self.settings.risk_percentage,
            daily_loss_limit=self.settings.daily_loss_limit,
            daily_profit_target=self.settings.daily_profit_target,
            max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "4")),
        )
        self.execution_engine = PaperExecutionEngine(db_session=self.db)
        self.telegram = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

        self.ist = ZoneInfo("Asia/Kolkata")
        self.entry_start_time = dt_time(9, 15)
        self.square_off_time = dt_time(15, 20)

        if self.db.query(Watchlist).count() == 0:
            self.db.bulk_save_objects(
                [
                    Watchlist(symbol="NIFTY", exchange="NSE"),
                    Watchlist(symbol="BANKNIFTY", exchange="NSE"),
                ]
            )
            self.db.commit()

        self._sync_risk_state()

    @staticmethod
    def _env_list(name: str, default: list[str]) -> list[str]:
        value = os.getenv(name, "")
        if not value.strip():
            return default
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _symbol_key(symbol: str) -> str:
        key = (symbol or "").upper().replace(" ", "")
        if key in {"NIFTY50", "^NSEI"}:
            return "NIFTY"
        return key

    def _trade_block_override_enabled(self, symbol: str) -> bool:
        return self._symbol_key(symbol) in self.trade_block_override_symbols

    def _entry_time_ist(self, trade: Trade) -> datetime:
        entry_time = trade.entry_time
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        return entry_time.astimezone(self.ist)

    def _today_utc_range(self) -> tuple[datetime, datetime]:
        now_ist = datetime.now(self.ist)
        start_ist = datetime.combine(now_ist.date(), dt_time.min, tzinfo=self.ist)
        end_ist = start_ist + timedelta(days=1)
        return (
            start_ist.astimezone(timezone.utc).replace(tzinfo=None),
            end_ist.astimezone(timezone.utc).replace(tzinfo=None),
        )

    def _sync_risk_state(self) -> None:
        start_utc, end_utc = self._today_utc_range()

        daily_pnl = (
            self.db.query(func.coalesce(func.sum(Trade.profit_loss), 0.0))
            .filter(
                Trade.status == "CLOSED",
                Trade.exit_time >= start_utc,
                Trade.exit_time < end_utc,
            )
            .scalar()
        )
        open_trades = self.db.query(Trade).filter(Trade.status == "OPEN").all()
        open_risk = sum(self._remaining_trade_risk(trade) for trade in open_trades)
        self.risk_manager.sync_state(
            daily_pnl=daily_pnl,
            open_trades_count=len(open_trades),
            open_risk=open_risk,
        )

    def _remaining_trade_risk(self, trade: Trade) -> float:
        risk_price = trade.current_sl if trade.current_sl is not None else trade.stop_loss
        if risk_price is None:
            return 0.0

        if trade.trade_type == "BUY":
            risk_per_unit = max(0.0, trade.entry_price - risk_price)
        else:
            risk_per_unit = max(0.0, risk_price - trade.entry_price)
        return risk_per_unit * trade.quantity

    def _trade_metadata(self, trade: Trade) -> dict:
        return parse_trade_metadata(trade.notes)

    def _display_symbol(self, trade: Trade) -> str:
        return display_symbol(trade.symbol, self._trade_metadata(trade))

    def _latest_underlying_price(self, symbol: str) -> float | None:
        df = self.fetcher.fetch_live_data(symbol, "NSE", "1m")
        if df is None or df.empty:
            return None
        return float(df.iloc[-1]["close"])

    def _latest_price(self, trade: Trade) -> float:
        metadata = self._trade_metadata(trade)
        if is_option_execution(metadata):
            option_ltp = get_live_nse_option_ltp(
                trade.symbol,
                metadata["opt_type"],
                metadata["strike"],
                metadata.get("expiry"),
            )
            if option_ltp is not None and option_ltp > 0:
                return float(option_ltp)

            spot = self._latest_underlying_price(trade.symbol)
            underlying_entry = metadata.get("underlying_entry")
            if spot is not None and underlying_entry:
                direction = trade_direction(trade.trade_type, metadata)
                spot_move = spot - underlying_entry if direction == "BUY" else underlying_entry - spot
                return round(max(0.05, trade.entry_price + (spot_move * 0.45)), 2)

            print(f"Could not fetch {self._display_symbol(trade)}; using entry premium.")
            return float(trade.entry_price)

        spot = self._latest_underlying_price(trade.symbol)
        if spot is not None:
            return spot
        print(f"Could not fetch {trade.symbol}; using entry price.")
        return float(trade.entry_price)

    def _close_trade(self, trade: Trade, exit_price: float, reason: str) -> Trade | None:
        symbol = self._display_symbol(trade)
        closed = self.execution_engine.close_position(trade.id, exit_price, reason)
        if not closed:
            return None

        self._sync_risk_state()
        is_loss = bool(closed.profit_loss is not None and closed.profit_loss < 0)
        self.telegram.send_exit_alert(symbol, reason, exit_price, is_loss=is_loss)
        print(
            f"Closed {symbol} trade id={closed.id} at {closed.exit_price} "
            f"reason={reason} pnl={closed.profit_loss}"
        )
        return closed

    def _square_off_required(self, trade: Trade, now_ist: datetime) -> str | None:
        if self._entry_time_ist(trade).date() < now_ist.date():
            return "Stale Intraday Auto Exit"
        if now_ist.time() >= self.square_off_time:
            return "Intraday Square Off"
        return None

    def _square_off_open_trades(self) -> bool:
        now_ist = datetime.now(self.ist)
        open_trades = self.db.query(Trade).filter(Trade.status == "OPEN").all()
        closed_any = False

        for trade in open_trades:
            reason = self._square_off_required(trade, now_ist)
            if reason:
                self._close_trade(trade, self._latest_price(trade), reason)
                closed_any = True

        return closed_any

    def _track_open_trades(self) -> None:
        open_trades = self.db.query(Trade).filter(Trade.status == "OPEN").all()
        if open_trades:
            print(f"Tracking {len(open_trades)} open positions...")

        for trade in open_trades:
            ltp = self._latest_price(trade)
            symbol = self._display_symbol(trade)

            metadata = self._trade_metadata(trade)
            
            if trade.stop_loss:
                trail_distance = abs(trade.entry_price - trade.stop_loss)
            else:
                trail_distance = 15.0

            if trade.trade_type == "BUY":
                new_sl = round(ltp - trail_distance, 2)
                if trade.current_sl is None or new_sl > trade.current_sl:
                    previous_sl = trade.current_sl if trade.current_sl is not None else trade.stop_loss
                    last_alert_sl = metadata.get("last_alert_sl", trade.stop_loss or 0)
                    trade.current_sl = new_sl
                    self.db.commit()
                    
                    if new_sl - last_alert_sl >= 5.0:
                        metadata["last_alert_sl"] = new_sl
                        trade.notes = serialize_trade_metadata(metadata)
                        self.db.commit()
                        self.telegram.send_trailing_sl_alert(
                            symbol=symbol,
                            previous_sl=previous_sl,
                            new_sl=new_sl,
                            current_price=ltp,
                        )

                if trade.current_sl is not None and ltp <= trade.current_sl:
                    self._close_trade(trade, trade.current_sl, "Trailing SL Hit")
                elif trade.target_2 is not None and ltp >= trade.target_2:
                    self._close_trade(trade, trade.target_2, "Target 2 Hit")

            elif trade.trade_type == "SELL":
                new_sl = round(ltp + trail_distance, 2)
                if trade.current_sl is None or new_sl < trade.current_sl:
                    previous_sl = trade.current_sl if trade.current_sl is not None else trade.stop_loss
                    last_alert_sl = metadata.get("last_alert_sl", trade.stop_loss or float('inf'))
                    trade.current_sl = new_sl
                    self.db.commit()
                    
                    if last_alert_sl - new_sl >= 5.0:
                        metadata["last_alert_sl"] = new_sl
                        trade.notes = serialize_trade_metadata(metadata)
                        self.db.commit()
                        self.telegram.send_trailing_sl_alert(
                            symbol=symbol,
                            previous_sl=previous_sl,
                            new_sl=new_sl,
                            current_price=ltp,
                        )

                if trade.current_sl is not None and ltp >= trade.current_sl:
                    self._close_trade(trade, trade.current_sl, "Trailing SL Hit")
                elif trade.target_2 is not None and ltp <= trade.target_2:
                    self._close_trade(trade, trade.target_2, "Target 2 Hit")

    def _create_signal_record(self, signal_data: dict) -> Signal:
        signal = Signal(
            symbol=signal_data["symbol"],
            signal_type=signal_data["signal"],
            entry_price=float(signal_data["entry"]),
            stop_loss=float(signal_data["stop_loss"]),
            target_1=float(signal_data["target_1"]),
            target_2=float(signal_data["target_2"]),
            target_3=float(signal_data["target_3"]),
            confidence_score=int(signal_data["confidence"]),
            status="PENDING",
        )
        self.db.add(signal)
        self.db.commit()
        self.db.refresh(signal)
        return signal

    def _set_signal_status(self, signal: Signal, status: str) -> None:
        signal.status = status
        self.db.commit()

    def _daily_trade_count(self) -> int:
        start_utc, end_utc = self._today_utc_range()
        return (
            self.db.query(Trade)
            .filter(Trade.entry_time >= start_utc, Trade.entry_time < end_utc)
            .count()
        )

    def _has_recent_loss(self, symbol: str, timeframe: str) -> bool:
        if self.loss_cooldown_minutes <= 0:
            return False

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=self.loss_cooldown_minutes
        )
        recent_losses = (
            self.db.query(Trade)
            .filter(
                Trade.symbol == symbol,
                Trade.status == "CLOSED",
                Trade.profit_loss < 0,
                Trade.exit_time >= cutoff,
            )
            .order_by(Trade.exit_time.desc())
            .all()
        )
        for trade in recent_losses:
            metadata = self._trade_metadata(trade)
            trade_timeframe = metadata.get("timeframe")
            if not trade_timeframe and (trade.strategy_used or "").startswith(
                "AI_Confluence_"
            ):
                trade_timeframe = trade.strategy_used.removeprefix("AI_Confluence_")
            if trade_timeframe == timeframe:
                return True
        return False

    def _open_trade_for_timeframe(
        self, symbol: str, timeframe: str
    ) -> Trade | None:
        open_trades = (
            self.db.query(Trade)
            .filter(Trade.symbol == symbol, Trade.status == "OPEN")
            .all()
        )
        for trade in open_trades:
            metadata = self._trade_metadata(trade)
            trade_timeframe = metadata.get("timeframe")
            if not trade_timeframe and (trade.strategy_used or "").startswith(
                "AI_Confluence_"
            ):
                trade_timeframe = trade.strategy_used.removeprefix("AI_Confluence_")
            if trade_timeframe == timeframe:
                return trade
        return None

    def _process_signal(self, item: Watchlist, timeframe: str, signal_data: dict) -> None:
        if timeframe not in self.timeframes:
            print(f"Skipping unsupported entry timeframe: {timeframe}")
            return
        if signal_data["action"] != "EXECUTE":
            return

        override_blocks = self._trade_block_override_enabled(item.symbol)
        signal_record = self._create_signal_record(signal_data)
        confidence = int(signal_data.get("confidence", 0))
        if confidence < self.min_trade_confidence and not override_blocks:
            self._set_signal_status(signal_record, "IGNORED")
            print(
                f"Skipping {item.symbol} {timeframe}: confidence {confidence} "
                f"below minimum {self.min_trade_confidence}."
            )
            return
        if confidence < self.min_trade_confidence:
            print(
                f"Trade block override for {item.symbol} {timeframe}: "
                f"confidence {confidence} below minimum {self.min_trade_confidence}."
            )

        daily_trade_limit_reached = (
            self.max_daily_trades > 0
            and self._daily_trade_count() >= self.max_daily_trades
        )
        if daily_trade_limit_reached and not override_blocks:
            self._set_signal_status(signal_record, "IGNORED")
            print(
                f"Skipping {item.symbol} {timeframe}: max daily trades "
                f"({self.max_daily_trades}) reached."
            )
            return
        if daily_trade_limit_reached:
            print(
                f"Trade block override for {item.symbol} {timeframe}: "
                f"max daily trades ({self.max_daily_trades}) reached."
            )

        has_recent_loss = self._has_recent_loss(item.symbol, timeframe)
        if has_recent_loss and not override_blocks:
            self._set_signal_status(signal_record, "IGNORED")
            print(
                f"Skipping {item.symbol} {timeframe}: loss cooldown active "
                f"for {self.loss_cooldown_minutes} minutes."
            )
            return
        if has_recent_loss:
            print(
                f"Trade block override for {item.symbol} {timeframe}: "
                f"loss cooldown active for {self.loss_cooldown_minutes} minutes."
            )

        existing_trade = self._open_trade_for_timeframe(item.symbol, timeframe)

        if existing_trade:
            existing_direction = trade_direction(
                existing_trade.trade_type,
                self._trade_metadata(existing_trade),
            )
            if existing_direction == signal_data["signal"]:
                self._set_signal_status(signal_record, "IGNORED")
                print(
                    f"Skipping {item.symbol} {timeframe}: "
                    f"{existing_direction} trade already open."
                )
                return

        self._sync_risk_state()
        risk_check = self.risk_manager.check_trade_allowed(
            replacing_open_trade=existing_trade is not None
        )
        if not risk_check["allowed"] and not override_blocks:
            self._set_signal_status(signal_record, "IGNORED")
            print(f"Trade blocked by risk management: {risk_check['reason']}")
            return
        if not risk_check["allowed"]:
            print(
                f"Trade block override for {item.symbol} {timeframe}: "
                f"{risk_check['reason']}"
            )

        if existing_trade:
            self._close_trade(
                existing_trade,
                self._latest_price(existing_trade),
                "Trend Reversal",
            )
            self._sync_risk_state()
            risk_check = self.risk_manager.check_trade_allowed()
            if not risk_check["allowed"] and not override_blocks:
                self._set_signal_status(signal_record, "IGNORED")
                print(f"Reversal entry blocked after exit: {risk_check['reason']}")
                return
            if not risk_check["allowed"]:
                print(
                    f"Trade block override for {item.symbol} {timeframe}: "
                    f"reversal entry risk check failed: {risk_check['reason']}"
                )

        options_info = build_options_info(
            symbol=item.symbol,
            signal=signal_data["signal"],
            spot_price=signal_data["entry"],
        )
        pos_info = self.risk_manager.calculate_position_size(
            entry_price=options_info["premium"],
            stop_loss=options_info["sl"],
            lot_size=options_info["lot_size"],
        )
        if pos_info["qty"] <= 0:
            if override_blocks and options_info["premium"] > 0:
                risk_per_unit = abs(options_info["premium"] - options_info["sl"])
                lot_size = max(1, int(options_info["lot_size"]))
                pos_info = {
                    **pos_info,
                    "qty": lot_size,
                    "capital_required": lot_size * options_info["premium"],
                    "max_loss": lot_size * risk_per_unit,
                    "forced_minimum_lot": True,
                }
                print(
                    f"Trade block override for {item.symbol} {timeframe}: "
                    f"{pos_info.get('error', 'zero quantity')} Using one lot."
                )
            else:
                self._set_signal_status(signal_record, "IGNORED")
                print(f"Trade blocked by position sizing: {pos_info.get('error', 'zero quantity')}")
                return

        if pos_info["qty"] <= 0:
            self._set_signal_status(signal_record, "IGNORED")
            print(f"Trade blocked by position sizing: {pos_info.get('error', 'zero quantity')}")
            return

        self._sync_risk_state()
        risk_check = self.risk_manager.check_trade_allowed(
            additional_risk=pos_info["max_loss"]
        )
        if not risk_check["allowed"] and not override_blocks:
            self._set_signal_status(signal_record, "IGNORED")
            print(f"Trade blocked by risk management: {risk_check['reason']}")
            return
        if not risk_check["allowed"]:
            print(
                f"Trade block override for {item.symbol} {timeframe}: "
                f"{risk_check['reason']}"
            )

        if not self.settings.paper_trading:
            self._set_signal_status(signal_record, "IGNORED")
            print("Live trading is not configured; signal was not executed.")
            return

        metadata = {
            **options_info,
            "execution_instrument": "OPTION",
            "signal_direction": signal_data["signal"],
            "underlying_entry": float(signal_data["entry"]),
            "timeframe": timeframe,
        }
        trade = self.execution_engine.place_market_order(
            symbol=signal_data["symbol"],
            action="BUY",
            quantity=pos_info["qty"],
            price=options_info["premium"],
            strategy=f"AI_Confluence_{timeframe}",
            sl=options_info["sl"],
            t1=options_info["t1"],
            t2=options_info["t2"],
            notes=serialize_trade_metadata(metadata),
        )
        self._set_signal_status(signal_record, "EXECUTED")
        self._sync_risk_state()
        self.telegram.send_trade_alert(signal_data, options_info)
        print(
            f"Option trade executed: {self._display_symbol(trade)} "
            f"BUY {trade.quantity} @ {trade.entry_price}"
        )

    def _run_cycle(self) -> None:
        if self._square_off_open_trades():
            return

        now_ist = datetime.now(self.ist)
        if now_ist.time() < self.entry_start_time:
            print("Waiting for post-open range to settle before taking entries.")
            return
        if now_ist.time() >= self.square_off_time:
            print("Intraday square-off time passed; skipping new entries.")
            return

        self._track_open_trades()
        active_symbols = (
            self.db.query(Watchlist).filter(Watchlist.is_active.is_(True)).all()
        )
        if self.symbol_filter:
            active_symbols = [
                item for item in active_symbols if item.symbol.upper() in self.symbol_filter
            ]
        if not active_symbols:
            print("Watchlist empty.")
            return

        for item in active_symbols:
            for timeframe in self.timeframes:
                try:
                    print(f"Fetching {item.symbol} on {timeframe}...")
                    df = self.fetcher.fetch_live_data(
                        item.symbol,
                        item.exchange,
                        timeframe,
                    )
                    if df is not None and not df.empty:
                        signal_data = self.ai_engine.generate_signal(df, item.symbol)
                        self._process_signal(item, timeframe, signal_data)
                except Exception:
                    self.db.rollback()
                    print(f"Failed processing {item.symbol} {timeframe}:")
                    traceback.print_exc()
                time.sleep(2)

    def run(self):
        print("Trading Daemon Started...")
        self.telegram.send_message(
            "<b>Algorithmic Trading Engine Started</b>\n\n"
            "Connected to market data and strategy engine."
        )

        while True:
            try:
                self._run_cycle()
            except KeyboardInterrupt:
                raise
            except Exception:
                self.db.rollback()
                print("Trading cycle failed; retrying next cycle:")
                traceback.print_exc()
            time.sleep(60)


if __name__ == "__main__":
    TradingDaemon().run()

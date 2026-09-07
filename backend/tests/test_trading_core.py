import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app_daemon import TradingDaemon
from backend.database import Base
from backend.execution_engine.paper_trading import PaperExecutionEngine
from backend.models import Signal, Trade, Watchlist
from backend.options_helper import _fallback_expiry, build_options_info
from backend.risk_management.risk_manager import RiskManager
from backend.trade_metadata import (
    display_symbol,
    is_option_execution,
    parse_trade_metadata,
    serialize_trade_metadata,
    trade_direction,
)


class RiskManagerTests(unittest.TestCase):
    def test_database_state_enforces_daily_loss_and_open_trade_limits(self):
        manager = RiskManager(daily_loss_limit=5000, max_open_trades=3)

        manager.sync_state(daily_pnl=-5000, open_trades_count=0)
        self.assertFalse(manager.check_trade_allowed()["allowed"])

        manager.sync_state(daily_pnl=0, open_trades_count=3)
        self.assertFalse(manager.check_trade_allowed()["allowed"])
        self.assertTrue(
            manager.check_trade_allowed(replacing_open_trade=True)["allowed"]
        )

    def test_option_position_size_is_rounded_to_whole_lots(self):
        manager = RiskManager(total_capital=100000, risk_per_trade_pct=1)
        result = manager.calculate_position_size(
            entry_price=100,
            stop_loss=70,
            lot_size=30,
        )

        self.assertEqual(result["qty"], 30)
        self.assertEqual(result["capital_required"], 3000)
        self.assertEqual(result["max_loss"], 900)

    def test_position_size_blocks_one_lot_when_it_exceeds_risk_limit(self):
        manager = RiskManager(total_capital=100000, risk_per_trade_pct=1)
        result = manager.calculate_position_size(
            entry_price=1000,
            stop_loss=850,
            lot_size=30,
        )

        self.assertEqual(result["qty"], 0)
        self.assertEqual(result["minimum_lot_risk"], 4500)

    def test_daily_risk_budget_includes_open_and_new_trade_risk(self):
        manager = RiskManager(daily_loss_limit=5000, max_open_trades=3)

        manager.sync_state(daily_pnl=0, open_trades_count=1, open_risk=4300)

        blocked = manager.check_trade_allowed(additional_risk=1000)
        allowed = manager.check_trade_allowed(additional_risk=500)

        self.assertFalse(blocked["allowed"])
        self.assertTrue(allowed["allowed"])


class PaperExecutionTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.engine = PaperExecutionEngine(self.db)

    def tearDown(self):
        self.db.close()

    def test_close_preserves_option_metadata_and_adds_reason(self):
        metadata = {
            "execution_instrument": "OPTION",
            "signal_direction": "SELL",
            "opt_type": "PE",
            "strike": 24000,
        }
        trade = self.engine.place_market_order(
            symbol="NIFTY",
            action="BUY",
            quantity=65,
            price=100,
            strategy="test",
            notes=serialize_trade_metadata(metadata),
        )

        closed = self.engine.close_position(trade.id, 120, "Manual Close")
        saved_metadata = parse_trade_metadata(closed.notes)

        self.assertEqual(closed.profit_loss, 1300)
        self.assertEqual(saved_metadata["close_reason"], "Manual Close")
        self.assertEqual(saved_metadata["strike"], 24000)
        self.assertEqual(trade_direction(closed.trade_type, saved_metadata), "SELL")
        self.assertEqual(display_symbol(closed.symbol, saved_metadata), "NIFTY 24000 PE")
        self.assertTrue(is_option_execution(saved_metadata))


class TimeframeEntryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.execution = PaperExecutionEngine(self.db)
        self.daemon = TradingDaemon.__new__(TradingDaemon)
        self.daemon.db = self.db

    def tearDown(self):
        self.db.close()

    def _place_trade(self, timeframe):
        return self.execution.place_market_order(
            symbol="NIFTY",
            action="BUY",
            quantity=65,
            price=100,
            strategy=f"AI_Confluence_{timeframe}",
            notes=serialize_trade_metadata(
                {"timeframe": timeframe, "signal_direction": "BUY"}
            ),
        )

    def test_open_trade_lookup_is_scoped_to_timeframe(self):
        five_minute_trade = self._place_trade("5m")
        fifteen_minute_trade = self._place_trade("15m")

        found_5m = self.daemon._open_trade_for_timeframe("NIFTY", "5m")
        found_15m = self.daemon._open_trade_for_timeframe("NIFTY", "15m")

        self.assertEqual(found_5m.id, five_minute_trade.id)
        self.assertEqual(found_15m.id, fifteen_minute_trade.id)

    def test_other_symbol_does_not_block_timeframe(self):
        self._place_trade("5m")
        self.assertIsNone(
            self.daemon._open_trade_for_timeframe("BANKNIFTY", "5m")
        )


class TradeBlockOverrideTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.execution = PaperExecutionEngine(self.db)
        self.daemon = TradingDaemon.__new__(TradingDaemon)
        self.daemon.db = self.db
        self.daemon.timeframes = ["5m"]
        self.daemon.min_trade_confidence = 90
        self.daemon.max_daily_trades = 1
        self.daemon.loss_cooldown_minutes = 30
        self.daemon.trade_block_override_symbols = {"NIFTY"}
        self.daemon.execution_engine = self.execution
        self.daemon.telegram = Mock()
        self.daemon.settings = SimpleNamespace(paper_trading=True)
        self.daemon._sync_risk_state = Mock()
        self.daemon._today_utc_range = Mock()
        self.daemon._daily_trade_count = Mock(return_value=5)
        self.daemon._has_recent_loss = Mock(return_value=True)

    def tearDown(self):
        self.db.close()

    @staticmethod
    def _signal(confidence=10):
        return {
            "action": "EXECUTE",
            "symbol": "NIFTY",
            "signal": "BUY",
            "entry": 24000,
            "stop_loss": 23950,
            "target_1": 24050,
            "target_2": 24100,
            "target_3": 24150,
            "confidence": confidence,
        }

    def test_nifty_override_executes_through_trade_blocks(self):
        self.daemon.risk_manager = Mock(
            check_trade_allowed=Mock(return_value={"allowed": False, "reason": "blocked"}),
            calculate_position_size=Mock(
                return_value={"qty": 0, "max_loss": 0, "error": "too small"}
            ),
        )
        options_info = {
            "opt_type": "CE",
            "strike": 24050,
            "expiry": "25 Aug 2026",
            "lot_size": 65,
            "premium": 100,
            "sl": 85,
            "t1": 180,
            "t2": 250,
        }

        with patch("backend.app_daemon.build_options_info", return_value=options_info):
            self.daemon._process_signal(Watchlist(symbol="NIFTY", exchange="NSE"), "5m", self._signal())

        trade = self.db.query(Trade).one()
        signal = self.db.query(Signal).one()
        self.assertEqual(trade.symbol, "NIFTY")
        self.assertEqual(trade.quantity, 65)
        self.assertEqual(signal.status, "EXECUTED")

    def test_non_override_symbol_still_blocks(self):
        self.daemon.risk_manager = Mock()

        self.daemon._process_signal(
            Watchlist(symbol="BANKNIFTY", exchange="NSE"),
            "5m",
            {**self._signal(), "symbol": "BANKNIFTY"},
        )

        self.assertEqual(self.db.query(Trade).count(), 0)
        self.assertEqual(self.db.query(Signal).one().status, "IGNORED")

    def test_nifty_50_alias_uses_nifty_override_key(self):
        self.assertEqual(self.daemon._symbol_key("NIFTY 50"), "NIFTY")
        self.assertTrue(self.daemon._trade_block_override_enabled("NIFTY 50"))



class ExpiryFallbackTests(unittest.TestCase):
    def test_nifty_fallback_uses_next_tuesday(self):
        from datetime import datetime

        expiry = _fallback_expiry("NIFTY", datetime(2026, 6, 22, 12, 0))
        self.assertEqual(expiry.date().isoformat(), "2026-06-23")

    def test_banknifty_fallback_uses_monthly_last_tuesday(self):
        from datetime import datetime

        expiry = _fallback_expiry("BANKNIFTY", datetime(2026, 6, 2, 12, 0))
        self.assertEqual(expiry.date().isoformat(), "2026-06-30")


class OptionsHelperTests(unittest.TestCase):
    @patch("backend.options_helper.get_live_nse_option_ltp", return_value=100)
    @patch("backend.options_helper.get_nearest_expiry", return_value="25 Aug 2026")
    def test_nifty_options_use_five_percent_stop_loss(self, *_):
        options_info = build_options_info("NIFTY", "BUY", 24000)

        self.assertEqual(options_info["sl"], 95)
        self.assertEqual(options_info["lot_size"], 65)

    @patch("backend.options_helper.get_live_nse_option_ltp", return_value=100)
    @patch("backend.options_helper.get_nearest_expiry", return_value="25 Aug 2026")
    def test_nifty_50_alias_uses_five_percent_stop_loss(self, *_):
        options_info = build_options_info("NIFTY 50", "BUY", 24000)

        self.assertEqual(options_info["sl"], 95)
        self.assertEqual(options_info["lot_size"], 65)

    @patch("backend.options_helper.get_live_nse_option_ltp", return_value=100)
    @patch("backend.options_helper.get_nearest_expiry", return_value="25 Aug 2026")
    def test_banknifty_keeps_existing_fifteen_percent_stop_loss(self, *_):
        options_info = build_options_info("BANKNIFTY", "BUY", 48000)

        self.assertEqual(options_info["sl"], 85)


if __name__ == "__main__":
    unittest.main()

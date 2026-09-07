from .ema_strategy import EMAStrategy
from .rsi_strategy import RSIStrategy
from .macd_strategy import MACDStrategy
from .breakout_strategy import BreakoutStrategy
from .advanced_strategies import (
    SupertrendStrategy, VWAPStrategy, BollingerSqueezeStrategy,
    ORBStrategy, MTFConfluenceStrategy, RSIMACDDivergenceStrategy, StochRSIStrategy
)
import pandas as pd
from .indicators import atr

class MultiConfirmationEngine:
    def __init__(self):
        self.trend_strategies = [
            SupertrendStrategy(),
            VWAPStrategy(),
            EMAStrategy(),
            MTFConfluenceStrategy()
        ]
        self.trigger_strategies = [
            RSIStrategy(),
            MACDStrategy(),
            BreakoutStrategy(),
            BollingerSqueezeStrategy(),
            ORBStrategy(),
            RSIMACDDivergenceStrategy(),
            StochRSIStrategy()
        ]
        self.strategies = self.trend_strategies + self.trigger_strategies

    def evaluate_all(self, df: pd.DataFrame) -> dict:
        results = {}
        
        # 1. Evaluate Primary Trend Filters
        bullish_trend_votes = 0
        bearish_trend_votes = 0
        active_trends = 0
        
        for strategy in self.trend_strategies:
            if not strategy.is_enabled:
                continue
            active_trends += 1
            res = strategy.evaluate(df)
            results[strategy.name] = res
            
            # Extract trend direction either from signal or details state
            sig = res.get("signal", "NEUTRAL")
            trend_state = res.get("details", {}).get("trend", "FLAT")
            
            if sig == "BUY" or "BULLISH" in trend_state:
                bullish_trend_votes += 1
            elif sig == "SELL" or "BEARISH" in trend_state:
                bearish_trend_votes += 1
                
        # Determine Master Trend (needs agreement from at least 2 trend indicators)
        master_trend = "NEUTRAL"
        if bullish_trend_votes >= 2 and bullish_trend_votes > bearish_trend_votes:
            master_trend = "BULLISH"
        elif bearish_trend_votes >= 2 and bearish_trend_votes > bullish_trend_votes:
            master_trend = "BEARISH"
            
        # 2. Evaluate Secondary Entry Triggers
        buy_triggers = 0
        sell_triggers = 0
        
        for strategy in self.trigger_strategies:
            if not strategy.is_enabled:
                continue
            res = strategy.evaluate(df)
            results[strategy.name] = res
            
            if res.get("signal") == "BUY":
                buy_triggers += 1
            elif res.get("signal") == "SELL":
                sell_triggers += 1
                
        # 3. Smart Confluence Logic
        overall_signal = "NEUTRAL"
        base_confidence = 0
        
        # Trade is taken only when trend has strong agreement and at least
        # two trigger strategies confirm the entry.
        if master_trend == "BULLISH" and bullish_trend_votes >= 3 and buy_triggers >= 2:
            overall_signal = "BUY"
            # Base confidence starts at 60 and scales with more confluence
            base_confidence = min(100, 60 + (buy_triggers * 10) + (bullish_trend_votes * 5))
        elif master_trend == "BEARISH" and bearish_trend_votes >= 3 and sell_triggers >= 2:
            overall_signal = "SELL"
            base_confidence = min(100, 60 + (sell_triggers * 10) + (bearish_trend_votes * 5))
            
        return {
            "overall_signal": overall_signal,
            "base_confidence": base_confidence,
            "buy_votes": buy_triggers,
            "sell_votes": sell_triggers,
            "strategy_breakdown": results,
            "master_trend": master_trend
        }

class AISignalEngine:
    def __init__(self):
        self.multi_engine = MultiConfirmationEngine()
        
    def generate_signal(self, df: pd.DataFrame, symbol: str) -> dict:
        confluence = self.multi_engine.evaluate_all(df)
        
        signal_type = confluence["overall_signal"]
        confidence = confluence["base_confidence"]
        
        if signal_type == "NEUTRAL" or confidence < 75:
            return {"action": "IGNORE", "reason": "Low Confidence or Neutral"}
            
        curr_price = df.iloc[-1]['close']
        atr = self._calculate_atr(df)
        
        # Risk Reward Calculation
        sl_points = atr * 1.5
        
        if signal_type == "BUY":
            entry = curr_price
            sl = entry - sl_points
            t1 = entry + (sl_points * 1)
            t2 = entry + (sl_points * 2)
            t3 = entry + (sl_points * 3)
        else:
            entry = curr_price
            sl = entry + sl_points
            t1 = entry - (sl_points * 1)
            t2 = entry - (sl_points * 2)
            t3 = entry - (sl_points * 3)
            
        return {
            "action": "EXECUTE",
            "signal": signal_type,
            "symbol": symbol,
            "entry": entry,
            "stop_loss": sl,
            "target_1": t1,
            "target_2": t2,
            "target_3": t3,
            "risk_reward": "1:3",
            "confidence": int(confidence),
            "timestamp": df.iloc[-1]['datetime'].isoformat() if 'datetime' in df.columns else None,
            "breakdown": confluence["strategy_breakdown"]
        }
        
    def _calculate_atr(self, df: pd.DataFrame, period=14):
        atr_values = atr(df, period)
        latest_atr = atr_values.iloc[-1]
        if not pd.isna(latest_atr):
            return latest_atr
        return df.iloc[-1]['close'] * 0.002

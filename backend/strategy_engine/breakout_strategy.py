import pandas as pd
from .base import BaseStrategy

class BreakoutStrategy(BaseStrategy):
    def __init__(self, lookback_period=20):
        super().__init__("Breakout Strategy")
        self.lookback = lookback_period

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < self.lookback + 1:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        # Calculate previous highs and lows
        # Not including the current candle
        past_df = df.iloc[-(self.lookback + 1):-1]
        recent_high = past_df['high'].max()
        recent_low = past_df['low'].min()
        avg_volume = past_df['volume'].mean()

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # Breakout Up
        if prev['close'] <= recent_high and curr['close'] > recent_high:
            vol_conf = 10 if curr['volume'] > avg_volume else 0
            return {"signal": "BUY", "confidence": 75 + vol_conf, "details": {"breakout": "HIGH", "level": recent_high}}
        
        # Breakout Down
        elif prev['close'] >= recent_low and curr['close'] < recent_low:
            vol_conf = 10 if curr['volume'] > avg_volume else 0
            return {"signal": "SELL", "confidence": 75 + vol_conf, "details": {"breakout": "LOW", "level": recent_low}}

        return {"signal": "NEUTRAL", "confidence": 0, "details": {"resistance": recent_high, "support": recent_low}}

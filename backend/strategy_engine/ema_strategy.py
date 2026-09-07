import pandas as pd
from .base import BaseStrategy
from .indicators import ema

class EMAStrategy(BaseStrategy):
    def __init__(self, short_period=9, long_period=21):
        super().__init__("EMA Crossover")
        self.short_period = short_period
        self.long_period = long_period

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < self.long_period + 1:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        df_calc = df.copy()
        df_calc[f'ema_{self.short_period}'] = ema(df_calc['close'], self.short_period)
        df_calc[f'ema_{self.long_period}'] = ema(df_calc['close'], self.long_period)

        prev = df_calc.iloc[-2]
        curr = df_calc.iloc[-1]

        short_prev = prev[f'ema_{self.short_period}']
        long_prev = prev[f'ema_{self.long_period}']
        short_curr = curr[f'ema_{self.short_period}']
        long_curr = curr[f'ema_{self.long_period}']

        if pd.isna(long_curr):
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        # Bullish Crossover
        if short_prev <= long_prev and short_curr > long_curr:
            return {"signal": "BUY", "confidence": 85, "details": {"trend": "BULLISH"}}
        
        # Bearish Crossover
        elif short_prev >= long_prev and short_curr < long_curr:
            return {"signal": "SELL", "confidence": 85, "details": {"trend": "BEARISH"}}
        
        # Trend continuation check (for multi-confirmation)
        if short_curr > long_curr:
            return {"signal": "NEUTRAL", "confidence": 50, "details": {"trend": "BULLISH"}}
        elif short_curr < long_curr:
            return {"signal": "NEUTRAL", "confidence": 50, "details": {"trend": "BEARISH"}}

        return {"signal": "NEUTRAL", "confidence": 0, "details": {"trend": "FLAT"}}

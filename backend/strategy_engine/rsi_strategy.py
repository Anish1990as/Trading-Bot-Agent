import pandas as pd
from .base import BaseStrategy
from .indicators import rsi

class RSIStrategy(BaseStrategy):
    def __init__(self, period=14, overbought=70, oversold=30):
        super().__init__("RSI Strategy")
        self.period = period
        self.overbought = overbought
        self.oversold = oversold

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < self.period + 1:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        df_calc = df.copy()
        df_calc['rsi'] = rsi(df_calc['close'], self.period)

        prev = df_calc.iloc[-2]
        curr = df_calc.iloc[-1]

        rsi_prev = prev['rsi']
        rsi_curr = curr['rsi']

        if pd.isna(rsi_curr):
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        # Buy Signal: Crossing above oversold
        if rsi_prev <= self.oversold and rsi_curr > self.oversold:
            return {"signal": "BUY", "confidence": 80, "details": {"rsi_value": rsi_curr}}
        
        # Sell Signal: Crossing below overbought
        elif rsi_prev >= self.overbought and rsi_curr < self.overbought:
            return {"signal": "SELL", "confidence": 80, "details": {"rsi_value": rsi_curr}}

        # State for multi-confirmation
        state = "NEUTRAL"
        if rsi_curr > 50:
            state = "BULLISH"
        elif rsi_curr < 50:
            state = "BEARISH"

        return {"signal": "NEUTRAL", "confidence": 0, "details": {"rsi_value": rsi_curr, "trend": state}}

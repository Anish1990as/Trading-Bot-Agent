import pandas as pd
from .base import BaseStrategy
from .indicators import macd

class MACDStrategy(BaseStrategy):
    def __init__(self, fast=12, slow=26, signal=9):
        super().__init__("MACD Strategy")
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < self.slow + self.signal:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        df_calc = df.copy()
        macd_line, signal_line, _ = macd(
            df_calc["close"],
            fast=self.fast,
            slow=self.slow,
            signal=self.signal,
        )
        df_calc["macd_line"] = macd_line
        df_calc["macd_signal"] = signal_line

        prev = df_calc.iloc[-2]
        curr = df_calc.iloc[-1]

        macd_prev = prev["macd_line"]
        sig_prev = prev["macd_signal"]
        macd_curr = curr["macd_line"]
        sig_curr = curr["macd_signal"]

        if pd.isna(macd_curr) or pd.isna(sig_curr):
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        # Bullish Crossover
        if macd_prev <= sig_prev and macd_curr > sig_curr:
            return {"signal": "BUY", "confidence": 85, "details": {"trend": "BULLISH"}}
        
        # Bearish Crossover
        elif macd_prev >= sig_prev and macd_curr < sig_curr:
            return {"signal": "SELL", "confidence": 85, "details": {"trend": "BEARISH"}}

        # Multi-confirmation state
        state = "NEUTRAL"
        if macd_curr > sig_curr and macd_curr > 0:
            state = "BULLISH"
        elif macd_curr < sig_curr and macd_curr < 0:
            state = "BEARISH"

        return {"signal": "NEUTRAL", "confidence": 0, "details": {"trend": state}}

import pandas as pd
from .base import BaseStrategy
from .indicators import (
    bollinger_bands,
    ema,
    macd,
    rsi,
    stochastic_rsi,
    supertrend_direction,
)


class SupertrendStrategy(BaseStrategy):
    """
    Supertrend Strategy - ATR-based trend follower.
    Very popular in Indian markets for intraday and swing trading.
    """
    def __init__(self, period=10, multiplier=3.0):
        super().__init__("Supertrend")
        self.period = period
        self.multiplier = multiplier

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < self.period + 5:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        direction = supertrend_direction(
            df,
            length=self.period,
            multiplier=self.multiplier,
        )
        curr_dir = direction.iloc[-1]
        prev_dir = direction.iloc[-2]

        # Fresh trend flip (highest confidence)
        if prev_dir == -1 and curr_dir == 1:
            return {"signal": "BUY", "confidence": 90, "details": {"trend": "BULLISH_FLIP"}}
        elif prev_dir == 1 and curr_dir == -1:
            return {"signal": "SELL", "confidence": 90, "details": {"trend": "BEARISH_FLIP"}}

        # Continuation (for multi-confirmation)
        trend = "BULLISH" if curr_dir == 1 else "BEARISH"
        return {"signal": "NEUTRAL", "confidence": 0, "details": {"trend": trend}}


class VWAPStrategy(BaseStrategy):
    """
    VWAP (Volume Weighted Average Price) Strategy.
    Best for intraday. Price above VWAP = bullish bias; below = bearish.
    """
    def __init__(self):
        super().__init__("VWAP Strategy")

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < 5 or 'volume' not in df.columns:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        df_calc = df.copy()

        # Calculate VWAP manually (cumulative)
        df_calc['typical_price'] = (df_calc['high'] + df_calc['low'] + df_calc['close']) / 3
        df_calc['tp_vol'] = df_calc['typical_price'] * df_calc['volume']
        df_calc['vwap'] = df_calc['tp_vol'].cumsum() / df_calc['volume'].cumsum()

        curr = df_calc.iloc[-1]
        prev = df_calc.iloc[-2]

        curr_price = curr['close']
        curr_vwap = curr['vwap']
        prev_price = prev['close']
        prev_vwap = prev['vwap']

        # BUY: Price just crossed above VWAP
        if prev_price <= prev_vwap and curr_price > curr_vwap:
            return {"signal": "BUY", "confidence": 80, "details": {"vwap": round(curr_vwap, 2), "trend": "BULLISH"}}

        # SELL: Price just crossed below VWAP
        elif prev_price >= prev_vwap and curr_price < curr_vwap:
            return {"signal": "SELL", "confidence": 80, "details": {"vwap": round(curr_vwap, 2), "trend": "BEARISH"}}

        # Continuation state for multi-confirmation
        trend = "BULLISH" if curr_price > curr_vwap else "BEARISH"
        return {"signal": "NEUTRAL", "confidence": 0, "details": {"vwap": round(curr_vwap, 2), "trend": trend}}


class BollingerSqueezeStrategy(BaseStrategy):
    """
    Bollinger Band Squeeze Strategy.
    When volatility compresses (bands squeeze), a large breakout is imminent.
    Buy when squeeze fires upward, sell when it fires downward.
    """
    def __init__(self, length=20, std=2.0):
        super().__init__("Bollinger Squeeze")
        self.length = length
        self.std = std

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < self.length + 5:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        df_calc = df.copy()
        lower, _, upper = bollinger_bands(
            df_calc["close"],
            length=self.length,
            standard_deviations=self.std,
        )
        df_calc["bbu"] = upper
        df_calc["bbl"] = lower
        df_calc['bb_width'] = df_calc['bbu'] - df_calc['bbl']

        # Squeeze: current width is near its 20-bar low
        recent_min_width = df_calc['bb_width'].iloc[-20:].min()
        curr_width = df_calc['bb_width'].iloc[-1]
        prev_width = df_calc['bb_width'].iloc[-2]

        curr = df_calc.iloc[-1]

        # Squeeze is expanding (breakout just happened)
        if prev_width <= recent_min_width * 1.05 and curr_width > prev_width * 1.1:
            if curr['close'] > df_calc['bbu'].iloc[-1]:
                return {"signal": "BUY", "confidence": 82, "details": {"pattern": "SQUEEZE_BREAKOUT_UP"}}
            elif curr['close'] < df_calc['bbl'].iloc[-1]:
                return {"signal": "SELL", "confidence": 82, "details": {"pattern": "SQUEEZE_BREAKOUT_DOWN"}}

        return {"signal": "NEUTRAL", "confidence": 0, "details": {"bb_width": round(curr_width, 2)}}


class ORBStrategy(BaseStrategy):
    """
    Opening Range Breakout (ORB) Strategy.
    Identifies the high/low of the first N candles and trades breakout.
    Best on 5m timeframe. N=3 covers the first 15 minutes.
    """
    def __init__(self, orb_candles=3):
        super().__init__("ORB Strategy")
        self.orb_candles = orb_candles

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < self.orb_candles + 2:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        # Get today's candles only
        if 'datetime' in df.columns:
            df_calc = df.copy()
            last_date = df_calc['datetime'].iloc[-1].date()
            today_df = df_calc[df_calc['datetime'].dt.date == last_date]

            if len(today_df) < self.orb_candles + 1:
                return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

            orb_high = today_df.iloc[:self.orb_candles]['high'].max()
            orb_low = today_df.iloc[:self.orb_candles]['low'].min()

            curr = today_df.iloc[-1]
            prev = today_df.iloc[-2]

            # Breakout above ORB High
            if prev['close'] <= orb_high and curr['close'] > orb_high:
                return {"signal": "BUY", "confidence": 85, "details": {"orb_high": orb_high, "orb_low": orb_low}}

            # Breakdown below ORB Low
            elif prev['close'] >= orb_low and curr['close'] < orb_low:
                return {"signal": "SELL", "confidence": 85, "details": {"orb_high": orb_high, "orb_low": orb_low}}

        return {"signal": "NEUTRAL", "confidence": 0, "details": {}}


class MTFConfluenceStrategy(BaseStrategy):
    """
    Multi-Timeframe Confluence Strategy.
    Checks if the short-term (fast EMA) trend aligns with a longer trend proxy.
    Uses EMA 50 as the HTF trend filter vs EMA 9/21 for LTF entry.
    """
    def __init__(self):
        super().__init__("MTF Confluence")

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < 55:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        df_calc = df.copy()
        df_calc['ema9'] = ema(df_calc['close'], 9)
        df_calc['ema21'] = ema(df_calc['close'], 21)
        df_calc['ema50'] = ema(df_calc['close'], 50)

        curr = df_calc.iloc[-1]
        prev = df_calc.iloc[-2]

        htf_bullish = curr['close'] > curr['ema50']
        ltf_cross_up = prev['ema9'] <= prev['ema21'] and curr['ema9'] > curr['ema21']
        ltf_cross_down = prev['ema9'] >= prev['ema21'] and curr['ema9'] < curr['ema21']

        # Perfect confluence: HTF bullish + LTF bullish crossover
        if htf_bullish and ltf_cross_up:
            return {"signal": "BUY", "confidence": 92, "details": {"htf": "BULLISH", "ltf": "CROSSOVER_UP"}}

        # Perfect confluence: HTF bearish + LTF bearish crossover
        if not htf_bullish and ltf_cross_down:
            return {"signal": "SELL", "confidence": 92, "details": {"htf": "BEARISH", "ltf": "CROSSOVER_DOWN"}}

        # Trend continuation state (no fresh cross)
        trend = "BULLISH" if htf_bullish and curr['ema9'] > curr['ema21'] else "BEARISH"
        return {"signal": "NEUTRAL", "confidence": 0, "details": {"trend": trend}}


class RSIMACDDivergenceStrategy(BaseStrategy):
    """
    RSI + MACD Divergence Strategy.
    Detects when price makes a new high/low but RSI doesn't (hidden divergence).
    This often precedes strong reversals.
    """
    def __init__(self):
        super().__init__("RSI-MACD Divergence")

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < 30:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        df_calc = df.copy()
        df_calc['rsi'] = rsi(df_calc['close'], 14)
        macd_line, _, _ = macd(df_calc["close"], fast=12, slow=26, signal=9)
        df_calc["macd_line"] = macd_line

        window = df_calc.iloc[-15:]
        price_high_now = window['high'].iloc[-1] >= window['high'].iloc[:-1].max()
        price_low_now = window['low'].iloc[-1] <= window['low'].iloc[:-1].min()
        rsi_high_now = window['rsi'].iloc[-1] >= window['rsi'].iloc[:-1].max()
        rsi_low_now = window['rsi'].iloc[-1] <= window['rsi'].iloc[:-1].min()

        # Bearish Divergence: Price new high, RSI fails to make new high
        if price_high_now and not rsi_high_now and window['rsi'].iloc[-1] > 60:
            return {"signal": "SELL", "confidence": 78, "details": {"divergence": "BEARISH", "rsi": round(window['rsi'].iloc[-1], 1)}}

        # Bullish Divergence: Price new low, RSI fails to make new low
        if price_low_now and not rsi_low_now and window['rsi'].iloc[-1] < 40:
            return {"signal": "BUY", "confidence": 78, "details": {"divergence": "BULLISH", "rsi": round(window['rsi'].iloc[-1], 1)}}

        return {"signal": "NEUTRAL", "confidence": 0, "details": {}}


class StochRSIStrategy(BaseStrategy):
    """
    Stochastic RSI Strategy.
    RSI of RSI - much more sensitive and faster than plain RSI.
    Excellent for detecting micro-momentum shifts in intraday trading.
    """
    def __init__(self, rsi_period=14, stoch_period=14, k=3, d=3):
        super().__init__("Stochastic RSI")
        self.rsi_period = rsi_period
        self.stoch_period = stoch_period
        self.k = k
        self.d = d

    def evaluate(self, df: pd.DataFrame) -> dict:
        if len(df) < self.rsi_period + self.stoch_period + 10:
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        df_calc = df.copy()
        k_line, d_line = stochastic_rsi(
            df_calc["close"],
            rsi_length=self.rsi_period,
            stochastic_length=self.stoch_period,
            smooth_k=self.k,
            smooth_d=self.d,
        )
        k_prev = k_line.iloc[-2]
        d_prev = d_line.iloc[-2]
        k_curr = k_line.iloc[-1]
        d_curr = d_line.iloc[-1]

        if pd.isna(k_curr) or pd.isna(d_curr):
            return {"signal": "NEUTRAL", "confidence": 0, "details": {}}

        # BUY: K crosses above D in oversold zone (<20)
        if k_prev <= d_prev and k_curr > d_curr and k_curr < 30:
            return {"signal": "BUY", "confidence": 80, "details": {"k": round(k_curr, 1), "d": round(d_curr, 1), "zone": "OVERSOLD"}}

        # SELL: K crosses below D in overbought zone (>80)
        if k_prev >= d_prev and k_curr < d_curr and k_curr > 70:
            return {"signal": "SELL", "confidence": 80, "details": {"k": round(k_curr, 1), "d": round(d_curr, 1), "zone": "OVERBOUGHT"}}

        trend = "BULLISH" if k_curr > 50 else "BEARISH"
        return {"signal": "NEUTRAL", "confidence": 0, "details": {"k": round(k_curr, 1), "trend": trend}}

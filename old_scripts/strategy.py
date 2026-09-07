import pandas as pd
import pandas_ta as ta
from config import TOTAL_CAPITAL, RISK_PER_TRADE
from options_engine import options_ai

# Globals to keep track of alerts so we don't spam
ALERTS_SENT = set()

# Dictionary to track active trades for Trailing Stop-Loss
# Format: {'Nifty 50': {'type': 'BUY', 'entry': 22400, 't1': 22450, 'sl': 22350, 'trailed': False}}
ACTIVE_TRADES = {}

def track_trade(instrument_name, timeframe, trade_type, entry, t1, sl):
    """
    Tracks the current trade and returns an exit alert when a new opposite
    signal reverses an existing active trade.
    """
    trade_key = f"{instrument_name}_{timeframe}"
    existing_trade = ACTIVE_TRADES.get(trade_key)
    reversal_alert = ""

    if existing_trade and existing_trade.get('type') != trade_type:
        old_option = "CE" if existing_trade.get('type') == "BUY" else "PE"
        new_option = "CE" if trade_type == "BUY" else "PE"
        reversal_alert = (
            f"\n\n🔁 *{instrument_name} [{timeframe}] TREND REVERSAL EXIT!*\n"
            f"Close active {existing_trade.get('type')} ({old_option}) trade at {entry:.1f}.\n"
            f"Reason: Opposite {trade_type} ({new_option}) signal generated.\n"
            f"Now taking fresh {trade_type} trade."
        )

    ACTIVE_TRADES[trade_key] = {
        'type': trade_type,
        'entry': entry,
        't1': t1,
        'sl': sl,
        'trailed': False
    }

    return reversal_alert

def get_lot_size(instrument_name):
    """Returns the options lot size for the given instrument."""
    if instrument_name == "Nifty 50": return 25
    if instrument_name == "BankNifty": return 15
    if instrument_name == "Sensex": return 10
    if instrument_name == "FinNifty": return 40
    return 1

def get_atm_strike(instrument_name, spot_price):
    """Calculates the At-The-Money (ATM) strike price based on the index step size."""
    if instrument_name == "Nifty 50":
        return int(round(spot_price / 50.0)) * 50
    elif instrument_name == "BankNifty":
        return int(round(spot_price / 100.0)) * 100
    elif instrument_name == "Sensex":
        return int(round(spot_price / 100.0)) * 100
    elif instrument_name == "FinNifty":
        return int(round(spot_price / 50.0)) * 50
    return int(round(spot_price))

def check_orb(df, instrument_name, timeframe):
    """
    Checks for Opening Range Breakout (first 15 mins).
    Returns an alert message if breakout happens, else None.
    """
    df['time_only'] = df['datetime'].dt.time
    
    # Get candles before 09:30
    first_15m = df[df['time_only'] < pd.to_datetime('09:30:00').time()]
    if first_15m.empty:
        return None
        
    orb_high = first_15m['high'].max()
    orb_low = first_15m['low'].min()
    
    latest = df.iloc[-1]
    
    # Check if we broke out AFTER 9:30
    if latest['time_only'] >= pd.to_datetime('09:30:00').time():
        alert_id = f"{instrument_name}_{timeframe}_ORB_{latest['datetime'].date()}"
        if alert_id in ALERTS_SENT:
            return None # Already sent today
            
        atr_val = latest['close'] * 0.002 # Fallback ATR
        
        def get_details(price, is_buy):
            sl_points = 1.5 * atr_val
            risk_amount = TOTAL_CAPITAL * RISK_PER_TRADE
            lot_size = get_lot_size(instrument_name)
            atm_strike = get_atm_strike(instrument_name, price)
            opt_type = "CE" if is_buy else "PE"
            
            prem = options_ai.calculate_premium_trade_advanced(
                instrument_name, atm_strike, opt_type, sl_points, sl_points, 
                risk_amount, lot_size, price
            )
            
            if is_buy:
                sl = price - sl_points
                t1 = price + sl_points
                reversal_msg = track_trade(instrument_name, timeframe, 'BUY', price, t1, sl)
            else:
                sl = price + sl_points
                t1 = price - sl_points
                reversal_msg = track_trade(instrument_name, timeframe, 'SELL', price, t1, sl)
            
            # Format comprehensive message
            msg = f"\n💵 LTP: ₹{prem['ltp']} | Bid: ₹{prem['bid']} | Ask: ₹{prem['ask']} (Spread: {prem['spread_pct']}%)"
            msg += f"\n🎯 Target 1: ₹{prem['t1']} | Target 2: ₹{prem['t2']}"
            msg += f"\n🛑 Stop Loss: ₹{prem['sl']}"
            msg += f"\n💰 Position: {prem['lots']} Lots (Risk: ₹{prem['risk']:.0f})"
            msg += f"\n\n📊 GREEKS:"
            msg += f"\n  Δ Delta: {prem['delta']} | Γ Gamma: {prem['gamma']}"
            msg += f"\n  ν Vega: {prem['vega']} | θ Theta: {prem['theta']} (₹{prem['time_decay']}/day)"
            msg += f"\n\n📈 OPTION DATA:"
            msg += f"\n  IV: {prem['iv']}% | OI: {prem['oi']:,} | Vol: {prem['volume']:,}"
            msg += f"\n  DTE: {int(prem['dte'])} days | Win Rate: {prem['win_rate']}%"
            msg = reversal_msg + msg
            
            return msg

        prev = df.iloc[-2]
        
        if prev['close'] <= orb_high and latest['close'] > orb_high:
            ALERTS_SENT.add(alert_id)
            atm_strike = get_atm_strike(instrument_name, latest['close'])
            details = get_details(latest['close'], True)
            return f"🚀 *{instrument_name} [{timeframe}] ORB BULLISH Breakout!*\nPrice crossed above 15-Min High.\nPrice: {latest['close']}\nHint: BUY {atm_strike} CE{details}"
            
        elif prev['close'] >= orb_low and latest['close'] < orb_low:
            ALERTS_SENT.add(alert_id)
            atm_strike = get_atm_strike(instrument_name, latest['close'])
            details = get_details(latest['close'], False)
            return f"📉 *{instrument_name} [{timeframe}] ORB BEARISH Breakout!*\nPrice crossed below 15-Min Low.\nPrice: {latest['close']}\nHint: BUY {atm_strike} PE{details}"
            
    return None

def check_volume_spike(df, instrument_name, timeframe, multiplier=3, period=20):
    """
    Checks if the latest volume is `multiplier` times the average volume of last `period` candles.
    """
    if len(df) < period + 1:
        return None
        
    # Calculate SMA of Volume
    # We use .copy() to avoid SettingWithCopyWarning
    df_calc = df.copy()
    df_calc['vol_sma'] = df_calc.ta.sma(close='volume', length=period)
    latest = df_calc.iloc[-1]
    
    if pd.isna(latest['vol_sma']):
        return None
        
    if latest['volume'] > (latest['vol_sma'] * multiplier):
        alert_id = f"{instrument_name}_{timeframe}_VOL_{latest['timestamp']}"
        if alert_id not in ALERTS_SENT:
            ALERTS_SENT.add(alert_id)
            return f"🔥 *{instrument_name} [{timeframe}] VOLUME SPIKE!*\nCurrent Volume is {multiplier}x higher than average.\nPrice: {latest['close']}"
            
    return None

def check_price_alerts(df, instrument_name, timeframe, target_price):
    """
    Checks if the current price crossed a specific target price.
    """
    if len(df) < 2:
        return None
        
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    
    # Crossed above or below
    if (prev['close'] < target_price <= curr['close']) or (prev['close'] > target_price >= curr['close']):
        alert_id = f"{instrument_name}_{timeframe}_PRICE_{target_price}_{curr['timestamp']}"
        if alert_id not in ALERTS_SENT:
            ALERTS_SENT.add(alert_id)
            return f"🔔 *{instrument_name} [{timeframe}] PRICE ALERT!*\nPrice crossed your target of {target_price}.\nCurrent: {curr['close']}"
            
    return None

def check_trend_signals(df, instrument_name, timeframe):
    """
    Provides clear BUY/SELL hints using EMA Crossover (9, 21), Supertrend, VWAP Filter, and 5M MTF.
    Also provides ATR-based Stop-Loss and Targets.
    """
    if len(df) < 22:
        return None
        
    df_calc = df.copy()
    
    # Temporarily set datetime index for VWAP and Resampling
    df_calc.set_index('datetime', inplace=True)
    
    try:
        # Calculate VWAP
        df_calc.ta.vwap(append=True)
        vwap_col = [c for c in df_calc.columns if 'VWAP' in c][0]
        
        # Calculate ATR
        df_calc.ta.atr(length=14, append=True)
        atr_col = [c for c in df_calc.columns if 'ATR' in c][0]
        
        # 5-Minute MTF Supertrend
        try:
            df_5m = df_calc.resample('5T').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna()
            df_5m.ta.supertrend(length=10, multiplier=3.0, append=True)
            st_cols = [c for c in df_5m.columns if 'SUPERTd' in c]
            if st_cols:
                latest_5m_trend = df_5m[st_cols[0]].iloc[-1]
            else:
                latest_5m_trend = 0 # 0 means "Not enough data, ignore MTF check"
        except Exception:
            latest_5m_trend = 0 # Default to neutral if resampling fails
            
    except Exception as e:
        print("Indicator calculation error:", e)
        return None
        
    df_calc.reset_index(inplace=True)

    # Calculate 9 EMA and 21 EMA
    df_calc['ema_9'] = ta.ema(df_calc['close'], length=9)
    df_calc['ema_21'] = ta.ema(df_calc['close'], length=21)
    
    # Calculate RSI
    df_calc['rsi'] = ta.rsi(df_calc['close'], length=14)
    
    # Calculate MACD
    df_calc.ta.macd(fast=12, slow=26, signal=9, append=True)
    macd_line = [c for c in df_calc.columns if c.startswith('MACD_')]
    macd_signal = [c for c in df_calc.columns if c.startswith('MACDs_')]
    macd_line = macd_line[0] if macd_line else None
    macd_signal = macd_signal[0] if macd_signal else None

    # Calculate Stochastic
    df_calc.ta.stoch(k=14, d=3, smooth_k=3, append=True)
    stoch_k = [c for c in df_calc.columns if c.startswith('STOCHk_')]
    stoch_d = [c for c in df_calc.columns if c.startswith('STOCHd_')]
    stoch_k = stoch_k[0] if stoch_k else None
    stoch_d = stoch_d[0] if stoch_d else None
    
    prev = df_calc.iloc[-2]
    curr = df_calc.iloc[-1]
    
    if pd.isna(curr['ema_21']) or pd.isna(curr['rsi']) or pd.isna(curr[vwap_col]):
        return None
        
    atr_val = curr[atr_col] if not pd.isna(curr[atr_col]) else (curr['close'] * 0.002) # Fallback ATR
    
    # Formatting helper for Targets & Position Sizing
    def get_trade_details(price, is_buy):
        sl_points = 1.5 * atr_val
        risk_amount = TOTAL_CAPITAL * RISK_PER_TRADE
        lot_size = get_lot_size(instrument_name)
        atm_strike = get_atm_strike(instrument_name, price)
        opt_type = "CE" if is_buy else "PE"
        
        prem = options_ai.calculate_premium_trade_advanced(
            instrument_name, atm_strike, opt_type, sl_points, sl_points, 
            risk_amount, lot_size, price
        )
        
        if is_buy:
            sl = price - sl_points
            t1 = price + sl_points
            reversal_msg = track_trade(instrument_name, timeframe, 'BUY', price, t1, sl)
        else:
            sl = price + sl_points
            t1 = price - sl_points
            reversal_msg = track_trade(instrument_name, timeframe, 'SELL', price, t1, sl)
        
        # Format comprehensive message
        msg = f"\n💵 LTP: ₹{prem['ltp']} | Bid: ₹{prem['bid']} | Ask: ₹{prem['ask']} (Spread: {prem['spread_pct']}%)"
        msg += f"\n🎯 Target 1: ₹{prem['t1']} | Target 2: ₹{prem['t2']}"
        msg += f"\n🛑 Stop Loss: ₹{prem['sl']}"
        msg += f"\n💰 Position: {prem['lots']} Lots (Risk: ₹{prem['risk']:.0f})"
        msg += f"\n\n📊 GREEKS:"
        msg += f"\n  Δ Delta: {prem['delta']} | Γ Gamma: {prem['gamma']}"
        msg += f"\n  ν Vega: {prem['vega']} | θ Theta: {prem['theta']} (₹{prem['time_decay']}/day)"
        msg += f"\n\n📈 OPTION DATA:"
        msg += f"\n  IV: {prem['iv']}% | OI: {prem['oi']:,} | Vol: {prem['volume']:,}"
        msg += f"\n  DTE: {int(prem['dte'])} days | Win Rate: {prem['win_rate']}%"
        msg = reversal_msg + msg
        
        return msg

    # Check for Bullish Crossover (9 EMA crosses ABOVE 21 EMA) -> Market going UP
    if prev['ema_9'] <= prev['ema_21'] and curr['ema_9'] > curr['ema_21']:
        if curr['close'] > curr[vwap_col] and latest_5m_trend in [1, 0]: # VWAP and MTF filter
            alert_id = f"{instrument_name}_{timeframe}_BUY_{curr['timestamp']}"
            if alert_id not in ALERTS_SENT:
                ALERTS_SENT.add(alert_id)
                atm_strike = get_atm_strike(instrument_name, curr['close'])
                hint = "RSI Overbought (70+) - market might reverse soon!" if curr['rsi'] > 70 else f"BUY {atm_strike} CE"
                details = get_trade_details(curr['close'], is_buy=True)
                return f"🟢 *{instrument_name} [{timeframe}] HIGH-PROBABILITY BUY!*\nMarket trend changing upwards (Above VWAP + 5M Bullish).\nPrice: {curr['close']}\nRSI: {curr['rsi']:.1f}\nHint: {hint}{details}"
            
    # Check for Bearish Crossover (9 EMA crosses BELOW 21 EMA) -> Market going DOWN
    if prev['ema_9'] >= prev['ema_21'] and curr['ema_9'] < curr['ema_21']:
        if curr['close'] < curr[vwap_col] and latest_5m_trend in [-1, 0]: # VWAP and MTF filter
            alert_id = f"{instrument_name}_{timeframe}_SELL_{curr['timestamp']}"
            if alert_id not in ALERTS_SENT:
                ALERTS_SENT.add(alert_id)
                atm_strike = get_atm_strike(instrument_name, curr['close'])
                hint = "RSI Oversold (30-) - market might reverse soon!" if curr['rsi'] < 30 else f"BUY {atm_strike} PE"
                details = get_trade_details(curr['close'], is_buy=False)
                return f"🔴 *{instrument_name} [{timeframe}] HIGH-PROBABILITY SELL!*\nMarket trend changing downwards (Below VWAP + 5M Bearish).\nPrice: {curr['close']}\nRSI: {curr['rsi']:.1f}\nHint: {hint}{details}"
            
    # Calculate Supertrend (10, 3) for extremely accurate Buy/Sell signals
    try:
        st_df = df_calc.ta.supertrend(length=10, multiplier=3.0)
        if st_df is not None and not st_df.empty:
            direction_col = [c for c in st_df.columns if 'SUPERTd' in c][0]
            prev_dir = st_df[direction_col].iloc[-2]
            curr_dir = st_df[direction_col].iloc[-1]
            
            # Supertrend turns GREEN (Buy)
            if prev_dir == -1 and curr_dir == 1:
                if curr['close'] > curr[vwap_col] and latest_5m_trend in [1, 0]:
                    alert_id = f"{instrument_name}_{timeframe}_ST_BUY_{curr['timestamp']}"
                    if alert_id not in ALERTS_SENT:
                        ALERTS_SENT.add(alert_id)
                        atm_strike = get_atm_strike(instrument_name, curr['close'])
                        details = get_trade_details(curr['close'], is_buy=True)
                        return f"⭐ *{instrument_name} [{timeframe}] SUPERTREND BUY!*\nSupertrend has turned GREEN (Uptrend starting).\nPrice: {curr['close']}\nHint: BUY {atm_strike} CE{details}"
                    
            # Supertrend turns RED (Sell)
            elif prev_dir == 1 and curr_dir == -1:
                if curr['close'] < curr[vwap_col] and latest_5m_trend in [-1, 0]:
                    alert_id = f"{instrument_name}_{timeframe}_ST_SELL_{curr['timestamp']}"
                    if alert_id not in ALERTS_SENT:
                        ALERTS_SENT.add(alert_id)
                        atm_strike = get_atm_strike(instrument_name, curr['close'])
                        details = get_trade_details(curr['close'], is_buy=False)
                        return f"⚠️ *{instrument_name} [{timeframe}] SUPERTREND SELL!*\nSupertrend has turned RED (Downtrend starting).\nPrice: {curr['close']}\nHint: BUY {atm_strike} PE{details}"
    except Exception as e:
        pass
        
    # MACD Check
    if macd_line and macd_signal:
        prev_m_line = prev[macd_line]
        prev_m_sig = prev[macd_signal]
        curr_m_line = curr[macd_line]
        curr_m_sig = curr[macd_signal]
        
        if not pd.isna(curr_m_line) and not pd.isna(curr_m_sig):
            # Bullish Crossover
            if prev_m_line <= prev_m_sig and curr_m_line > curr_m_sig:
                if curr['close'] > curr[vwap_col]: # VWAP filter
                    alert_id = f"{instrument_name}_{timeframe}_MACD_BUY_{curr['timestamp']}"
                    if alert_id not in ALERTS_SENT:
                        ALERTS_SENT.add(alert_id)
                        atm_strike = get_atm_strike(instrument_name, curr['close'])
                        details = get_trade_details(curr['close'], is_buy=True)
                        return f"🟢 *{instrument_name} [{timeframe}] MACD CROSSOVER BUY!*\nMACD crossed above Signal Line (Above VWAP).\nPrice: {curr['close']}\nHint: BUY {atm_strike} CE{details}"
                        
            # Bearish Crossover
            elif prev_m_line >= prev_m_sig and curr_m_line < curr_m_sig:
                if curr['close'] < curr[vwap_col]: # VWAP filter
                    alert_id = f"{instrument_name}_{timeframe}_MACD_SELL_{curr['timestamp']}"
                    if alert_id not in ALERTS_SENT:
                        ALERTS_SENT.add(alert_id)
                        atm_strike = get_atm_strike(instrument_name, curr['close'])
                        details = get_trade_details(curr['close'], is_buy=False)
                        return f"🔴 *{instrument_name} [{timeframe}] MACD CROSSOVER SELL!*\nMACD crossed below Signal Line (Below VWAP).\nPrice: {curr['close']}\nHint: BUY {atm_strike} PE{details}"

    # Stochastic Check
    if stoch_k and stoch_d:
        prev_k = prev[stoch_k]
        prev_d = prev[stoch_d]
        curr_k = curr[stoch_k]
        curr_d = curr[stoch_d]
        
        if not pd.isna(curr_k) and not pd.isna(curr_d):
            # Oversold Reversal (Buy)
            if prev_k < 20 and curr_k < 20:
                if prev_k <= prev_d and curr_k > curr_d:
                    alert_id = f"{instrument_name}_{timeframe}_STOCH_BUY_{curr['timestamp']}"
                    if alert_id not in ALERTS_SENT:
                        ALERTS_SENT.add(alert_id)
                        atm_strike = get_atm_strike(instrument_name, curr['close'])
                        details = get_trade_details(curr['close'], is_buy=True)
                        return f"🟢 *{instrument_name} [{timeframe}] STOCHASTIC OVERSOLD BUY!*\nStochastic Reversal from Oversold zone.\nPrice: {curr['close']}\nHint: BUY {atm_strike} CE{details}"
                        
            # Overbought Reversal (Sell)
            if prev_k > 80 and curr_k > 80:
                if prev_k >= prev_d and curr_k < curr_d:
                    alert_id = f"{instrument_name}_{timeframe}_STOCH_SELL_{curr['timestamp']}"
                    if alert_id not in ALERTS_SENT:
                        ALERTS_SENT.add(alert_id)
                        atm_strike = get_atm_strike(instrument_name, curr['close'])
                        details = get_trade_details(curr['close'], is_buy=False)
                        return f"🔴 *{instrument_name} [{timeframe}] STOCHASTIC OVERBOUGHT SELL!*\nStochastic Reversal from Overbought zone.\nPrice: {curr['close']}\nHint: BUY {atm_strike} PE{details}"

    df_calc.reset_index(inplace=True)
            
    return None

def check_trailing_sl(curr_price, instrument_name, timeframe):
    """
    Checks if an active trade has hit Target 1 to trail Stop Loss to Entry Price.
    """
    if f"{instrument_name}_{timeframe}" in ACTIVE_TRADES:
        trade = ACTIVE_TRADES[f"{instrument_name}_{timeframe}"]
        if not trade['trailed']:
            if trade['type'] == 'BUY' and curr_price >= trade['t1']:
                trade['trailed'] = True
                return f"🛡️ *{instrument_name} [{timeframe}] (CE Trade) TRAIL SL ALERT!*\nIndex hit Target 1 Spot ({trade['t1']:.1f})!\nMove your Premium Stop-Loss to Cost-to-Cost (Zero Risk)."
            elif trade['type'] == 'SELL' and curr_price <= trade['t1']:
                trade['trailed'] = True
                return f"🛡️ *{instrument_name} [{timeframe}] (PE Trade) TRAIL SL ALERT!*\nIndex hit Target 1 Spot ({trade['t1']:.1f})!\nMove your Premium Stop-Loss to Cost-to-Cost (Zero Risk)."
    return None

def check_sl_hit(curr_price, instrument_name, timeframe):
    """
    Checks if an active trade has hit the Stop Loss level.
    Removes trade from active tracking when SL is hit.
    """
    if f"{instrument_name}_{timeframe}" in ACTIVE_TRADES:
        trade = ACTIVE_TRADES[f"{instrument_name}_{timeframe}"]
        
        if trade['type'] == 'BUY':
            # For BUY (CE), SL is hit when price goes BELOW sl level
            if curr_price <= trade['sl']:
                ACTIVE_TRADES.pop(f"{instrument_name}_{timeframe}", None)  # Remove from active trades
                pnl = (trade['entry'] - curr_price) * 25  # Assuming 25 lot size for Nifty
                return f"⛔ *{instrument_name} [{timeframe}] (CE Trade) STOP LOSS HIT!*\nPrice broke below SL ({trade['sl']:.1f})\nCurrent Price: {curr_price:.1f}\nLoss: ₹{abs(pnl):.0f}\n\n⚠️ EXIT IMMEDIATELY - Trade Stopped Out!"
        else:  # SELL
            # For SELL (PE), SL is hit when price goes ABOVE sl level
            if curr_price >= trade['sl']:
                ACTIVE_TRADES.pop(f"{instrument_name}_{timeframe}", None)  # Remove from active trades
                pnl = (curr_price - trade['entry']) * 25  # Assuming 25 lot size for Nifty
                return f"⛔ *{instrument_name} [{timeframe}] (PE Trade) STOP LOSS HIT!*\nPrice broke above SL ({trade['sl']:.1f})\nCurrent Price: {curr_price:.1f}\nLoss: ₹{abs(pnl):.0f}\n\n⚠️ EXIT IMMEDIATELY - Trade Stopped Out!"
    return None

def check_advanced_strategies(df, instrument_name, timeframe):
    """
    Checks for Pro-Trader strategies: Bollinger Squeeze, RSI Divergence, Inside Bar.
    """
    if len(df) < 22:
        return []
        
    alerts = []
    df_calc = df.copy()
    
    try:
        df_calc.ta.vwap(append=True)
        vwap_col = [c for c in df_calc.columns if 'VWAP' in c][0]
        df_calc.ta.atr(length=14, append=True)
        atr_col = [c for c in df_calc.columns if 'ATR' in c][0]
        df_calc['ema_21'] = ta.ema(df_calc['close'], length=21)
        df_calc['rsi'] = ta.rsi(df_calc['close'], length=14)
        
        # Squeeze
        sqz_df = df_calc.ta.squeeze(lazybear=False, append=False)
        if sqz_df is not None and not sqz_df.empty:
            df_calc = pd.concat([df_calc, sqz_df], axis=1)
    except Exception as e:
        return []

    curr = df_calc.iloc[-1]
    prev = df_calc.iloc[-2]
    
    if pd.isna(curr['ema_21']) or pd.isna(curr['rsi']) or pd.isna(curr[vwap_col]):
        return []
        
    atr_val = curr[atr_col] if not pd.isna(curr[atr_col]) else (curr['close'] * 0.002)
    
    def get_details(price, is_buy):
        sl_points = 1.5 * atr_val
        risk_amount = TOTAL_CAPITAL * RISK_PER_TRADE
        lot_size = get_lot_size(instrument_name)
        atm_strike = get_atm_strike(instrument_name, price)
        opt_type = "CE" if is_buy else "PE"
        
        prem = options_ai.calculate_premium_trade_advanced(
            instrument_name, atm_strike, opt_type, sl_points, sl_points, 
            risk_amount, lot_size, price
        )
        
        if is_buy:
            sl = price - sl_points
            t1 = price + sl_points
            reversal_msg = track_trade(instrument_name, timeframe, 'BUY', price, t1, sl)
        else:
            sl = price + sl_points
            t1 = price - sl_points
            reversal_msg = track_trade(instrument_name, timeframe, 'SELL', price, t1, sl)
        
        # Format comprehensive message
        msg = f"\n💵 LTP: ₹{prem['ltp']} | Bid: ₹{prem['bid']} | Ask: ₹{prem['ask']} (Spread: {prem['spread_pct']}%)"
        msg += f"\n🎯 Target 1: ₹{prem['t1']} | Target 2: ₹{prem['t2']}"
        msg += f"\n🛑 Stop Loss: ₹{prem['sl']}"
        msg += f"\n💰 Position: {prem['lots']} Lots (Risk: ₹{prem['risk']:.0f})"
        msg += f"\n\n📊 GREEKS:"
        msg += f"\n  Δ Delta: {prem['delta']} | Γ Gamma: {prem['gamma']}"
        msg += f"\n  ν Vega: {prem['vega']} | θ Theta: {prem['theta']} (₹{prem['time_decay']}/day)"
        msg += f"\n\n📈 OPTION DATA:"
        msg += f"\n  IV: {prem['iv']}% | OI: {prem['oi']:,} | Vol: {prem['volume']:,}"
        msg += f"\n  DTE: {int(prem['dte'])} days | Win Rate: {prem['win_rate']}%"
        msg = reversal_msg + msg
        
        return msg

    # 1. Bollinger Squeeze Breakout
    sqz_on_col = [c for c in df_calc.columns if 'SQZ_ON' in c]
    sqz_off_col = [c for c in df_calc.columns if 'SQZ_OFF' in c]
    if sqz_on_col and sqz_off_col:
        # If prev was ON (1) and curr is OFF (1), squeeze fired!
        if prev[sqz_on_col[0]] == 1 and curr[sqz_off_col[0]] == 1:
            alert_id = f"{instrument_name}_{timeframe}_SQZ_{curr['timestamp']}"
            if alert_id not in ALERTS_SENT:
                ALERTS_SENT.add(alert_id)
                atm_strike = get_atm_strike(instrument_name, curr['close'])
                if curr['close'] > curr['ema_21']:
                    details = get_details(curr['close'], True)
                    alerts.append(f"💥 *{instrument_name} [{timeframe}] BOLLINGER SQUEEZE BUY!*\nVolatility Breakout upwards.\nPrice: {curr['close']}\nHint: BUY {atm_strike} CE{details}")
                elif curr['close'] < curr['ema_21']:
                    details = get_details(curr['close'], False)
                    alerts.append(f"💥 *{instrument_name} [{timeframe}] BOLLINGER SQUEEZE SELL!*\nVolatility Breakout downwards.\nPrice: {curr['close']}\nHint: BUY {atm_strike} PE{details}")

    # 2. RSI Divergence
    if len(df_calc) >= 11:
        past_10 = df_calc.iloc[-11]
        price_trend = curr['close'] - past_10['close']
        rsi_trend = curr['rsi'] - past_10['rsi']
        
        # Bearish Divergence (Price Up, RSI Down, Overbought)
        if price_trend > 0 and rsi_trend < 0 and curr['rsi'] > 65:
            alert_id = f"{instrument_name}_{timeframe}_BEARDIV_{curr['timestamp']}"
            if alert_id not in ALERTS_SENT:
                ALERTS_SENT.add(alert_id)
                atm_strike = get_atm_strike(instrument_name, curr['close'])
                details = get_details(curr['close'], False)
                alerts.append(f"🦅 *{instrument_name} [{timeframe}] RSI BEARISH DIVERGENCE!*\nPrice making higher highs but RSI is dropping. Reversal imminent!\nPrice: {curr['close']}\nHint: BUY {atm_strike} PE{details}")
        
        # Bullish Divergence (Price Down, RSI Up, Oversold)
        elif price_trend < 0 and rsi_trend > 0 and curr['rsi'] < 35:
            alert_id = f"{instrument_name}_{timeframe}_BULLDIV_{curr['timestamp']}"
            if alert_id not in ALERTS_SENT:
                ALERTS_SENT.add(alert_id)
                atm_strike = get_atm_strike(instrument_name, curr['close'])
                details = get_details(curr['close'], True)
                alerts.append(f"🦅 *{instrument_name} [{timeframe}] RSI BULLISH DIVERGENCE!*\nPrice making lower lows but RSI is rising. Reversal imminent!\nPrice: {curr['close']}\nHint: BUY {atm_strike} CE{details}")

    # 3. Inside Bar Breakout
    if len(df_calc) >= 3:
        mother = df_calc.iloc[-3]
        baby = df_calc.iloc[-2]
        
        # Check if baby is an inside bar
        if baby['high'] < mother['high'] and baby['low'] > mother['low']:
            # Breakout UP
            if curr['close'] > mother['high']:
                alert_id = f"{instrument_name}_{timeframe}_INSIDEBUY_{curr['timestamp']}"
                if alert_id not in ALERTS_SENT:
                    ALERTS_SENT.add(alert_id)
                    atm_strike = get_atm_strike(instrument_name, curr['close'])
                    details = get_details(curr['close'], True)
                    alerts.append(f"🕯️ *{instrument_name} [{timeframe}] INSIDE BAR BUY BREAKOUT!*\nPrice broke above the Mother Candle.\nPrice: {curr['close']}\nHint: BUY {atm_strike} CE{details}")
            # Breakout DOWN
            elif curr['close'] < mother['low']:
                alert_id = f"{instrument_name}_{timeframe}_INSIDESELL_{curr['timestamp']}"
                if alert_id not in ALERTS_SENT:
                    ALERTS_SENT.add(alert_id)
                    atm_strike = get_atm_strike(instrument_name, curr['close'])
                    details = get_details(curr['close'], False)
                    alerts.append(f"🕯️ *{instrument_name} [{timeframe}] INSIDE BAR SELL BREAKOUT!*\nPrice broke below the Mother Candle.\nPrice: {curr['close']}\nHint: BUY {atm_strike} PE{details}")

    return alerts

def evaluate_all_strategies(df, instrument_name, timeframe="5m", custom_alerts=None, market_context=None):
    """
    Evaluates all strategies on the dataframe and returns a list of alert messages.
    """
    alerts = []
    if df is None or df.empty:
        return alerts
        
    curr_price = df.iloc[-1]['close']
    
    # 0. Check SL Hit FIRST (most critical)
    sl_hit_alert = check_sl_hit(curr_price, instrument_name, timeframe)
    if sl_hit_alert: alerts.append(sl_hit_alert)
    
    # 1. Trailing SL Check (only if SL not already hit)
    trail_alert = check_trailing_sl(curr_price, instrument_name, timeframe)
    if trail_alert: alerts.append(trail_alert)
        
    # 1. Trailing SL Check (only if SL not already hit)
    trail_alert = check_trailing_sl(curr_price, instrument_name, timeframe)
    if trail_alert: alerts.append(trail_alert)
        
    # 2. ORB Check
    orb_alert = check_orb(df, instrument_name, timeframe)
    if orb_alert: alerts.append(orb_alert)
    
    # 3. Volume Spike Check
    vol_alert = check_volume_spike(df, instrument_name, timeframe)
    if vol_alert: alerts.append(vol_alert)
    
    # 4. Price Alerts Check
    if custom_alerts and instrument_name in custom_alerts:
        for target in custom_alerts[instrument_name]:
            price_alert = check_price_alerts(df, instrument_name, timeframe, target)
            if price_alert: alerts.append(price_alert)
            
    # 5. Trend Hints (Buy/Sell signals)
    trend_alert = check_trend_signals(df, instrument_name, timeframe)
    if trend_alert: alerts.append(trend_alert)
            
    # 6. Advanced Pro-Trader Strategies
    adv_alerts = check_advanced_strategies(df, instrument_name, timeframe)
    alerts.extend(adv_alerts)
    
    # --- Append Market Context ---
    if market_context and alerts:
        # Get Option Chain Context
        opt_ctx = options_ai.fetch_option_chain_context(instrument_name, curr_price)
        
        context_str = f"\n\n--- 📊 MARKET CONTEXT ---\n"
        context_str += f"📰 News Sentiment: {market_context.get('news_sentiment', 'Neutral')}\n"
        context_str += f"🌍 Global Mood (S&P500): {market_context.get('global_mood', 'Neutral')} ({market_context.get('sp500_change', 0)}%)\n"
        context_str += f"📈 India VIX: {market_context.get('vix', 15.0)}"
        if market_context.get('vix', 15.0) > 20:
            context_str += " (High Volatility - Reduce Size!)"
        context_str += f"\n📅 Daily Trend: {market_context.get('daily_trends', {}).get(instrument_name, 'Neutral')}"
        
        context_str += f"\n\n--- 🧠 OPTION CHAIN AI ---\n"
        context_str += f"⚖️ PCR: {opt_ctx['pcr']} ({opt_ctx['sentiment']})\n"
        context_str += f"🎯 Max Pain: {opt_ctx['max_pain']}\n"
        context_str += f"🧱 Resistance (Call OI): {opt_ctx['resistance']}\n"
        context_str += f"🛡️ Support (Put OI): {opt_ctx['support']}"
        
        final_alerts = []
        for alert in alerts:
            is_buy = "BUY" in alert or "BULLISH" in alert
            is_sell = "SELL" in alert or "BEARISH" in alert
            
            caution_msg = ""
            if is_buy or is_sell:
                daily_trend = market_context.get('daily_trends', {}).get(instrument_name, 'Neutral')
                news_sent = market_context.get('news_sentiment', 'Neutral')
                
                conflicts = []
                if is_buy:
                    if daily_trend == "Bearish": conflicts.append("Daily Trend is Bearish")
                    if news_sent == "Bearish": conflicts.append("News Sentiment is Bearish")
                    if opt_ctx['pcr'] < 0.8: conflicts.append("Option Chain is Bearish")
                if is_sell:
                    if daily_trend == "Bullish": conflicts.append("Daily Trend is Bullish")
                    if news_sent == "Bullish": conflicts.append("News Sentiment is Bullish")
                    if opt_ctx['pcr'] > 1.2: conflicts.append("Option Chain is Bullish")
                    
                if conflicts:
                    caution_msg = f"\n\n⚠️ CAUTION: You are trading against the macro context! ({', '.join(conflicts)})"
                
            # Don't append context to Trailing SL alerts
            if "TRAIL SL" in alert:
                final_alerts.append(alert)
            else:
                final_alerts.append(alert + caution_msg + context_str)
                
        return final_alerts
            
    return alerts

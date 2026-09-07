import yfinance as yf
import pandas as pd
import pandas_ta as ta

INSTRUMENTS = {
    "Nifty 50": "^NSEI",
    "BankNifty": "^NSEBANK",
    "Sensex": "^BSESN"
}

def backtest_instrument(instrument_name, symbol):
    print(f"\nFetching historical data for {instrument_name} ({symbol}) (last 60 days, 5-minute interval)...")
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="60d", interval="5m")
    
    if df.empty:
        print(f"Failed to fetch data for {instrument_name}.")
        return
        
    df.reset_index(inplace=True)
    
    # Clean up column names for pandas_ta
    df.rename(columns={'Datetime': 'datetime', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
    
    df.set_index('datetime', inplace=True)
    df.ta.vwap(append=True)
    df.ta.atr(length=14, append=True)
    df.ta.supertrend(length=10, multiplier=3.0, append=True)
    
    vwap_col = [c for c in df.columns if 'VWAP' in c]
    atr_col = [c for c in df.columns if 'ATR' in c]
    st_col = [c for c in df.columns if 'SUPERTd' in c]
    
    if not vwap_col or not atr_col or not st_col:
        print(f"Not enough data to calculate indicators for {instrument_name}.")
        return
        
    vwap_col = vwap_col[0]
    atr_col = atr_col[0]
    st_col = st_col[0]
    
    df.reset_index(inplace=True)
    df['ema_9'] = ta.ema(df['close'], length=9)
    df['ema_21'] = ta.ema(df['close'], length=21)
    
    # Backtest Loop
    trades = []
    in_position = False
    entry_price = 0
    trade_type = ""
    stop_loss = 0
    target_1 = 0
    
    for i in range(25, len(df)):
        prev = df.iloc[i-1]
        curr = df.iloc[i]
        
        if pd.isna(curr['ema_21']) or pd.isna(curr[vwap_col]):
            continue
            
        # Manage active position
        if in_position:
            if trade_type == 'BUY':
                if curr['low'] <= stop_loss:
                    trades.append({'type': 'BUY', 'pnl': stop_loss - entry_price, 'exit': 'SL'})
                    in_position = False
                elif curr['high'] >= target_1:
                    trades.append({'type': 'BUY', 'pnl': target_1 - entry_price, 'exit': 'T1'})
                    in_position = False
            elif trade_type == 'SELL':
                if curr['high'] >= stop_loss:
                    trades.append({'type': 'SELL', 'pnl': entry_price - stop_loss, 'exit': 'SL'})
                    in_position = False
                elif curr['low'] <= target_1:
                    trades.append({'type': 'SELL', 'pnl': entry_price - target_1, 'exit': 'T1'})
                    in_position = False
            continue
            
        # Entry Logic
        atr_val = curr[atr_col] if not pd.isna(curr[atr_col]) else 20
        
        # BUY condition
        if prev['ema_9'] <= prev['ema_21'] and curr['ema_9'] > curr['ema_21']:
            if curr['close'] > curr[vwap_col] and curr[st_col] == 1:
                in_position = True
                trade_type = 'BUY'
                entry_price = curr['close']
                stop_loss = entry_price - (1.5 * atr_val)
                target_1 = entry_price + (1.5 * atr_val)
                
        # SELL condition
        elif prev['ema_9'] >= prev['ema_21'] and curr['ema_9'] < curr['ema_21']:
            if curr['close'] < curr[vwap_col] and curr[st_col] == -1:
                in_position = True
                trade_type = 'SELL'
                entry_price = curr['close']
                stop_loss = entry_price + (1.5 * atr_val)
                target_1 = entry_price - (1.5 * atr_val)

    # Calculate Results
    print("="*40)
    print(f"BACKTEST RESULTS: {instrument_name} (Last 60 Days)")
    print("="*40)
    
    if len(trades) == 0:
        print("No trades executed.")
        print("="*40)
        return
        
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    
    total_pnl_points = sum([t['pnl'] for t in trades])
    win_rate = (len(wins) / len(trades)) * 100
    
    print(f"Total Trades Taken : {len(trades)}")
    print(f"Winning Trades     : {len(wins)}")
    print(f"Losing Trades      : {len(losses)}")
    print(f"Win Rate           : {win_rate:.2f}%")
    print(f"Net Points PnL     : {total_pnl_points:.2f} points")
    
    # Estimate rupees based on lot size
    lot_size = 25 if instrument_name == "Nifty 50" else (15 if instrument_name == "BankNifty" else 10)
    net_rupees = total_pnl_points * lot_size
    print(f"Estimated Net PnL  : ₹{net_rupees:.2f} (Trading 1 Lot)")
    print("="*40)

def run_all_backtests():
    for name, symbol in INSTRUMENTS.items():
        backtest_instrument(name, symbol)

if __name__ == "__main__":
    run_all_backtests()

import pandas as pd
import pytz
import sys
import io

# Suppress tvDatafeed print statements and logs
import logging
logging.getLogger("tvDatafeed.main").setLevel(logging.CRITICAL)

old_stdout = sys.stdout
sys.stdout = io.StringIO()
from tvDatafeed import TvDatafeed, Interval
# Initialize tvDatafeed globally
tv = TvDatafeed()
sys.stdout = old_stdout

# Instrument Definitions mapping name to TradingView symbol and exchange
INSTRUMENTS = {
    "Nifty 50": {"symbol": "NIFTY", "exchange": "NSE"},
    "BankNifty": {"symbol": "BANKNIFTY", "exchange": "NSE"},
    "FinNifty": {"symbol": "CNXFIN", "exchange": "NSE"},
    "Sensex": {"symbol": "SENSEX", "exchange": "BSE"}
}

def fetch_intraday_data(instrument_name, timeframe='5m'):
    """
    Fetches the historical intraday data for the given instrument using tvDatafeed.
    Returns a pandas DataFrame.
    """
    global tv
    if instrument_name not in INSTRUMENTS:
        print(f"Unknown instrument: {instrument_name}")
        return pd.DataFrame()
        
    inst = INSTRUMENTS[instrument_name]
    
    tv_interval = Interval.in_5_minute if timeframe == '5m' else Interval.in_15_minute
    
    try:
        # Download data for the last 500 bars
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        df = tv.get_hist(symbol=inst['symbol'], exchange=inst['exchange'], interval=tv_interval, n_bars=500)
        
        # Auto-reconnect if connection dropped
        if df is None or df.empty:
            tv = TvDatafeed()
            df = tv.get_hist(symbol=inst['symbol'], exchange=inst['exchange'], interval=tv_interval, n_bars=500)
            
        sys.stdout = old_stdout
        
        if df is None or df.empty:
            return pd.DataFrame()
            
        # Reset index to get 'datetime' as a column
        df = df.reset_index()
        
        # Convert timezone
        ist = pytz.timezone('Asia/Kolkata')
        if df['datetime'].dt.tz is None:
            df['datetime'] = df['datetime'].dt.tz_localize('Asia/Kolkata')
        else:
            df['datetime'] = df['datetime'].dt.tz_convert(ist)
            
        # Create a basic timestamp column
        df['timestamp'] = df['datetime'].astype('int64') // 10**9
        
        df = df.sort_values(by='datetime').reset_index(drop=True)
        return df
            
    except Exception as e:
        print(f"Exception while fetching data for {instrument_name}: {e}")
        return pd.DataFrame()

import time
from datetime import datetime

from telegram_bot import format_market_context_summary, send_telegram_message
from market_data import INSTRUMENTS, fetch_intraday_data
from strategy import evaluate_all_strategies
from market_context import get_market_context

# Define custom price alerts here (Target Prices)
CUSTOM_ALERTS = {
    "Nifty 50": [22000, 23000],
    "BankNifty": [47000, 48000, 49000]
}

def main():
    print("Starting Multi-Strategy Intraday Scanner (Free yfinance Edition)...")
    
    msg = "SUCCESS: Scanner Connected to TradingView (tvDatafeed)!\nRunning for Nifty, BankNifty, FinNifty, Sensex."
    safe_print = msg.encode('ascii', 'ignore').decode('ascii')
    print(safe_print)
    send_telegram_message("✅ Scanner Connected to TradingView!\nRunning for Nifty, BankNifty, FinNifty, Sensex.")

    print("Setup is complete. Running scanner loop every 60 seconds...")

    last_context_fetch = 0
    current_context = None

    # Infinite Loop for scanning
    while True:
        now = datetime.now()
        print(f"\n[{now.strftime('%H:%M:%S')}] Scanning Markets...")
        
        # Only run between 9:15 AM and 3:30 PM (roughly)
        # You can add a time check here if needed.
        
        # Fetch market context every 15 minutes (900 seconds)
        if time.time() - last_context_fetch > 900:
            print(f"[{now.strftime('%H:%M:%S')}] Fetching Market Context (News, VIX, Global Mood, Daily Trends)...")
            current_context = get_market_context()
            last_context_fetch = time.time()
            print(f"[{now.strftime('%H:%M:%S')}] Market Context Updated: News={current_context.get('news_sentiment')}, VIX={current_context.get('vix')}")
            summary_msg = format_market_context_summary(current_context)
            if summary_msg:
                send_telegram_message(summary_msg)
        
        for instrument_name in INSTRUMENTS.keys():
            for timeframe in ['5m', '15m']:
                # 1. Fetch live data for timeframe
                df = fetch_intraday_data(instrument_name, timeframe=timeframe)
                
                if df is not None and not df.empty:
                    # 2. Evaluate Strategies
                    alerts = evaluate_all_strategies(df, instrument_name, timeframe=timeframe, custom_alerts=CUSTOM_ALERTS, market_context=current_context)
                    
                    # 3. Send Alerts
                    for alert_msg in alerts:
                        safe_print = alert_msg.encode('ascii', 'ignore').decode('ascii')
                        print(safe_print)
                        send_telegram_message(alert_msg)
                else:
                    print(f"  - No data fetched for {instrument_name} ({timeframe}).")
                    
                # Sleep 2 seconds between fetching each instrument/timeframe to avoid rate limits
                time.sleep(2)
                
        # Wait 60 seconds before next scan
        time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScanner stopped by user. Goodbye!")

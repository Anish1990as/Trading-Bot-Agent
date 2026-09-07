import datetime
import yfinance as yf
import pandas as pd
import urllib.request
import xml.etree.ElementTree as ET
from market_data import INSTRUMENTS
from tvDatafeed import TvDatafeed, Interval
import sys
import io
import logging

logging.getLogger("tvDatafeed.main").setLevel(logging.CRITICAL)
old_stdout = sys.stdout
sys.stdout = io.StringIO()
tv = TvDatafeed()
sys.stdout = old_stdout

# Simple sentiment keywords
BULLISH_WORDS = ['surge', 'rally', 'jump', 'gain', 'high', 'up', 'bull', 'soar', 'record', 'green', 'boost', 'climb']
BEARISH_WORDS = ['crash', 'fall', 'drop', 'slump', 'low', 'down', 'bear', 'plunge', 'red', 'loss', 'selloff', 'streak', 'wipe']

def fetch_news_sentiment():
    try:
        url = 'https://news.google.com/rss/search?q=indian+stock+market+OR+nifty+OR+sensex&hl=en-IN&gl=IN&ceid=IN:en'
        req = urllib.request.urlopen(url, timeout=5)
        tree = ET.parse(req)
        
        headlines = [item.find('title').text for item in tree.getroot().findall('.//item')][:5]
        
        bull_score = 0
        bear_score = 0
        
        for h in headlines:
            lower_h = h.lower()
            for w in BULLISH_WORDS:
                # adding spaces around to match whole words roughly, or just check substring
                # substring is fine but can have false positives, let's use simple substring for now
                if w in lower_h.split(): 
                    bull_score += 1
            for w in BEARISH_WORDS:
                if w in lower_h.split(): 
                    bear_score += 1
                
        sentiment = "Neutral"
        if bull_score > bear_score:
            sentiment = "Bullish"
        elif bear_score > bull_score:
            sentiment = "Bearish"
            
        return sentiment, headlines
    except Exception as e:
        print(f"Error fetching news: {e}")
        return "Neutral", []

def fetch_vix():
    try:
        df = yf.Ticker('^INDIAVIX').history(period='5d')
        df = df.dropna(subset=['Close'])
        if not df.empty:
            return round(df['Close'].iloc[-1], 2)
    except Exception as e:
        print(f"Error fetching VIX: {e}")
    return 15.0 # default if fails

def fetch_global_mood():
    try:
        # S&P 500
        df = yf.Ticker('^GSPC').history(period='5d') 
        df = df.dropna(subset=['Close'])
        if len(df) >= 2:
            prev_close = df['Close'].iloc[-2]
            curr_close = df['Close'].iloc[-1]
            change_pct = ((curr_close - prev_close) / prev_close) * 100
            sentiment = "Bullish" if change_pct >= 0 else "Bearish"
            return sentiment, round(change_pct, 2)
    except Exception as e:
        print(f"Error fetching global mood: {e}")
    return "Neutral", 0.0

def fetch_daily_trends():
    global tv
    trends = {}
    try:
        for name, inst in INSTRUMENTS.items():
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            df = tv.get_hist(symbol=inst['symbol'], exchange=inst['exchange'], interval=Interval.in_daily, n_bars=20)
            
            # Auto reconnect
            if df is None or df.empty:
                tv = TvDatafeed()
                df = tv.get_hist(symbol=inst['symbol'], exchange=inst['exchange'], interval=Interval.in_daily, n_bars=20)
                
            sys.stdout = old_stdout
            
            if df is None or len(df) < 9:
                trends[name] = "Neutral"
            else:
                # Use a simple 9 SMA vs Close for daily trend
                sma9 = df['close'].rolling(window=9).mean().iloc[-1]
                close = df['close'].iloc[-1]
                if close > sma9:
                    trends[name] = "Bullish"
                else:
                    trends[name] = "Bearish"
    except Exception as e:
        print(f"Error fetching daily trends: {e}")
        for name in INSTRUMENTS.keys():
            if name not in trends:
                trends[name] = "Neutral"
    return trends

def get_market_context():
    news_sent, headlines = fetch_news_sentiment()
    vix = fetch_vix()
    global_mood, sp500_change = fetch_global_mood()
    daily_trends = fetch_daily_trends()
    
    return {
        'news_sentiment': news_sent,
        'news_headlines': headlines,
        'vix': vix,
        'global_mood': global_mood,
        'sp500_change': sp500_change,
        'daily_trends': daily_trends,
        'last_updated': datetime.datetime.now().strftime("%H:%M:%S")
    }

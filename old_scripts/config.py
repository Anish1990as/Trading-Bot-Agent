import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Dhan API Credentials
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

# Telegram Bot Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Check if keys are loaded
if not DHAN_CLIENT_ID or not DHAN_ACCESS_TOKEN:
    print("WARNING: Dhan API credentials not found in .env file.")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("WARNING: Telegram credentials not found in .env file.")

# Advanced Algo Settings
TOTAL_CAPITAL = float(os.getenv("TOTAL_CAPITAL", "50000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.02")) # 2% max risk
TRADE_ALERT_MIN_CONFIDENCE = int(os.getenv("TRADE_ALERT_MIN_CONFIDENCE", "90"))

# Expiry settings
# Weekday values: Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4.
# Exact dates can be supplied as YYYY-MM-DD when a holiday or special expiry applies.
EXPIRY_WEEKDAYS = {
    "Nifty 50": int(os.getenv("NIFTY_EXPIRY_WEEKDAY", "1")),
    "BankNifty": int(os.getenv("BANKNIFTY_EXPIRY_WEEKDAY", "1")),
    "FinNifty": int(os.getenv("FINNIFTY_EXPIRY_WEEKDAY", "1")),
    "Sensex": int(os.getenv("SENSEX_EXPIRY_WEEKDAY", "3")),
}

EXPIRY_DATES = {
    "Nifty 50": os.getenv("NIFTY_EXPIRY_DATE", ""),
    "BankNifty": os.getenv("BANKNIFTY_EXPIRY_DATE", ""),
    "FinNifty": os.getenv("FINNIFTY_EXPIRY_DATE", ""),
    "Sensex": os.getenv("SENSEX_EXPIRY_DATE", ""),
}

# Dhan option-chain underlying IDs. Fill these from Dhan's instrument master.
DHAN_UNDERLYING_SECURITY_IDS = {
    "Sensex": os.getenv("SENSEX_UNDERLYING_SECURITY_ID", ""),
}

DHAN_UNDERLYING_SEGMENTS = {
    "Sensex": os.getenv("SENSEX_UNDERLYING_SEGMENT", "BSE_FNO"),
}

import math
import calendar
import os
import time
from threading import Lock
from datetime import datetime, timedelta, time as dt_time

import pandas as pd
import requests
from dotenv import load_dotenv


# The dashboard requests both the open-trade and recent-trade endpoints at the
# same time. Cache a successful NSE quote so those duplicate requests do not
# trigger another option-chain call (and so a brief NSE throttling response does
# not turn the current price into a blank cell).
# Keep the quote fresh for the dashboard while still coalescing its concurrent
# open/recent-trade requests into one NSE option-chain lookup.
_OPTION_LTP_CACHE_TTL_SECONDS = 10
_option_ltp_cache: dict[tuple[str, str, int, str | None], tuple[float, float]] = {}
_option_ltp_cache_lock = Lock()

# NSE index derivative lot sizes effective for January 2026 contracts onward.
LOT_SIZES = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "SENSEX": 20,
    "FINNIFTY": 60,
    "MIDCPNIFTY": 120,
}

# NSE index derivatives expire on Tuesday (1=Tuesday).
EXPIRY_WEEKDAYS = {
    "NIFTY": 1,
    "BANKNIFTY": 1,
    "SENSEX": 3,
    "FINNIFTY": 1,
    "MIDCPNIFTY": 1,
}


def _symbol_key(symbol: str) -> str:
    key = (symbol or "").upper().replace(" ", "")
    if key in {"NIFTY50", "^NSEI"}:
        return "NIFTY"
    return key


def _parse_expiry_date(value: str) -> datetime | None:
    for date_format in ("%d-%b-%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, date_format)
        except (TypeError, ValueError):
            continue
    return None


def _last_weekday_of_month(year: int, month: int, weekday: int) -> datetime:
    last_day = calendar.monthrange(year, month)[1]
    candidate = datetime(year, month, last_day)
    return candidate - timedelta(days=(candidate.weekday() - weekday) % 7)


def _fallback_expiry(symbol: str, now: datetime) -> datetime:
    weekday = EXPIRY_WEEKDAYS.get(symbol.upper(), 1)
    if symbol.upper() in {"NIFTY", "SENSEX"}:
        days = (weekday - now.weekday()) % 7
        # On expiry day after 1:00 PM, roll over to next weekly expiry to avoid 0-DTE theta decay crush
        if days == 0 and now.time() > dt_time(13, 0):
            days = 7
        return now + timedelta(days=days)

    expiry = _last_weekday_of_month(now.year, now.month, weekday)
    if expiry.date() < now.date() or (
        expiry.date() == now.date() and now.time() > dt_time(13, 0)
    ):
        next_month = 1 if now.month == 12 else now.month + 1
        next_year = now.year + 1 if now.month == 12 else now.year
        expiry = _last_weekday_of_month(next_year, next_month, weekday)
    return expiry


_scrip_master_cache: pd.DataFrame | None = None
_scrip_master_load_time: float = 0.0


def _get_scrip_master() -> pd.DataFrame:
    global _scrip_master_cache, _scrip_master_load_time
    now = time.time()
    if _scrip_master_cache is not None and (now - _scrip_master_load_time) < 3600:
        return _scrip_master_cache

    local_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "scrip_master_fno.csv")
    if os.path.exists(local_path) and (now - os.path.getmtime(local_path)) < 86400:
        try:
            _scrip_master_cache = pd.read_csv(local_path, low_memory=False)
            _scrip_master_load_time = now
            return _scrip_master_cache
        except Exception:
            pass

    try:
        df = pd.read_csv("https://images.dhan.co/api-data/api-scrip-master.csv", low_memory=False)
        fno = df[df["SEM_INSTRUMENT_NAME"].isin(["OPTIDX", "INDEX"])].copy()
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        fno.to_csv(local_path, index=False)
        _scrip_master_cache = fno
        _scrip_master_load_time = now
        return _scrip_master_cache
    except Exception:
        if _scrip_master_cache is not None:
            return _scrip_master_cache
        return pd.DataFrame()


def get_nearest_expiry(symbol: str) -> str:
    now = datetime.now()
    return _fallback_expiry(symbol, now).strftime("%d %b %Y")


def get_atm_strike(spot_price: float, symbol: str) -> int:
    """Rounds spot price to nearest ATM strike based on index."""
    symbol_upper = symbol.upper()
    if "SENSEX" in symbol_upper:
        step = 100
    elif "BANK" in symbol_upper:
        step = 100
    elif "MIDCP" in symbol_upper:
        step = 25
    else:
        step = 50  # NIFTY, FINNIFTY
    return int(round(spot_price / step) * step)


def get_otm_strike(spot_price: float, signal: str, symbol: str) -> int:
    """
    Returns a slightly OTM strike.
    BUY signal → OTM CE (1 strike above ATM)
    SELL signal → OTM PE (1 strike below ATM)
    """
    symbol_upper = symbol.upper()
    if "SENSEX" in symbol_upper:
        step = 100
    elif "BANK" in symbol_upper:
        step = 100
    elif "MIDCP" in symbol_upper:
        step = 25
    else:
        step = 50

    atm = get_atm_strike(spot_price, symbol)

    if signal == "BUY":
        return atm + step  # OTM Call
    else:
        return atm - step  # OTM Put


def _cached_option_ltp(cache_key: tuple[str, str, int, str | None]) -> float | None:
    with _option_ltp_cache_lock:
        cached = _option_ltp_cache.get(cache_key)

    if cached is None:
        return None

    price, fetched_at = cached
    if time.monotonic() - fetched_at <= _OPTION_LTP_CACHE_TTL_SECONDS:
        return price
    return None


def _store_option_ltp(cache_key: tuple[str, str, int, str | None], price: float) -> None:
    with _option_ltp_cache_lock:
        _option_ltp_cache[cache_key] = (price, time.monotonic())


def get_dhan_option_ltp(security_id: str, exchange_segment: str = "NSE_FNO") -> float | None:
    """Fetch an option LTP from Dhan's market quote endpoint with rate-limit protection and caching."""
    if not security_id:
        return None

    cache_key = (str(security_id), exchange_segment, 0, None)
    cached = _cached_option_ltp(cache_key)
    if cached is not None:
        return cached

    load_dotenv(override=True)
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token:
        return None

    try:
        response = requests.post(
            "https://api.dhan.co/v2/marketfeed/ltp",
            headers={
                "access-token": access_token,
                "client-id": client_id,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={exchange_segment: [int(security_id)]},
            timeout=(2.0, 5.0),
        )
        if response.status_code == 200:
            data = response.json().get("data", {}).get(exchange_segment, {})
            value = data.get(str(security_id), {}).get("last_price")
            if value and float(value) > 0:
                price = float(value)
                _store_option_ltp(cache_key, price)
                return price
        elif response.status_code == 429:
            time.sleep(0.5)
    except Exception:
        pass
    return None


def get_dhan_option_security_id(
    symbol: str,
    opt_type: str,
    strike: int,
    expiry: str,
) -> str | None:
    """Resolve the exact Dhan option contract ID from Dhan's scrip master."""
    try:
        scrips = _get_scrip_master()
        if scrips.empty:
            return None
        symbol_key = _symbol_key(symbol)
        exchange_id = "BSE" if symbol_key == "SENSEX" else "NSE"
        contracts = scrips[
            (scrips["SEM_EXM_EXCH_ID"] == exchange_id)
            & (scrips["SEM_INSTRUMENT_NAME"] == "OPTIDX")
            & (scrips["SEM_OPTION_TYPE"] == opt_type.upper())
            & (scrips["SEM_STRIKE_PRICE"].astype(float) == float(strike))
        ]
        contracts = contracts[
            contracts["SEM_TRADING_SYMBOL"].astype(str).str.startswith(symbol_key)
        ]
        if contracts.empty:
            return None
        contracts = contracts.copy()
        contracts["expiry"] = pd.to_datetime(
            contracts["SEM_EXPIRY_DATE"], errors="coerce"
        ).dt.strftime("%d %b %Y")
        match = contracts[contracts["expiry"] == expiry]
        if match.empty:
            return str(contracts.iloc[0]["SEM_SMST_SECURITY_ID"])
        return str(match.iloc[0]["SEM_SMST_SECURITY_ID"])
    except Exception:
        return None


def estimate_option_premium(spot: float, strike: int, signal: str, days_to_expiry: int = 5) -> float:
    """
    Simple Black-Scholes approximation for rough premium estimate.
    Uses native math.erf to avoid heavy scipy dependency.
    """
    T = max(0.001, days_to_expiry / 365.0)
    sigma = 0.18  # ~18% IV assumption
    r = 0.07

    def norm_cdf(x):
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        if signal == "BUY":  # CE
            premium = spot * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2)
        else:  # PE
            premium = strike * math.exp(-r * T) * norm_cdf(-d2) - spot * norm_cdf(-d1)

        return round(max(5.0, premium), 1)
    except Exception:
        return round(spot * 0.005, 1)  # ~0.5% of spot as fallback


def build_options_info(symbol: str, signal: str, spot_price: float) -> dict:
    """
    Builds a full options recommendation object from a directional signal.
    """
    symbol_key = _symbol_key(symbol)
    opt_type = "CE" if signal == "BUY" else "PE"
    strike = get_otm_strike(spot_price, signal, symbol_key)
    expiry = get_nearest_expiry(symbol_key)
    lot_size = LOT_SIZES.get(symbol_key, 1)
    
    # Calculate days to expiry dynamically based on the parsed expiry
    now = datetime.now()
    try:
        expiry_date = datetime.strptime(expiry, "%d %b %Y")
        days = (expiry_date.date() - now.date()).days
        if days == 0 and now.time() > dt_time(15, 30):
            days = 0.1
        elif days == 0:
            days = 0.5
        elif days < 0:
            days = 0.1
    except Exception:
        fallback_expiry = _fallback_expiry(symbol, now)
        days = (fallback_expiry.date() - now.date()).days
        if days == 0:
            days = 0.1 if now.time() > dt_time(15, 30) else 0.5

    est_premium = estimate_option_premium(spot_price, strike, signal, days)

    # Look up live security ID and real-time LTP from Dhan
    security_id = get_dhan_option_security_id(symbol_key, opt_type, strike, expiry)
    segment = "BSE_FNO" if symbol_key == "SENSEX" else "NSE_FNO"
    real_ltp = get_dhan_option_ltp(security_id or "", segment) if security_id else None
    premium = float(real_ltp) if real_ltp and real_ltp > 0 else float(est_premium)
    
    stop_loss_risk_pct = 0.18
    sl = round(premium * (1 - stop_loss_risk_pct), 1)
    t1 = round(premium * 1.25, 1)
    t2 = round(premium * 1.60, 1)

    return {
        "opt_type": opt_type,
        "strike": strike,
        "expiry": expiry,
        "lot_size": lot_size,
        "premium": premium,
        "security_id": security_id,
        "sl": sl,
        "t1": t1,
        "t2": t2,
    }

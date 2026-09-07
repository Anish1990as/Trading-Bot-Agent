import math
import calendar
import time
from threading import Lock
from datetime import datetime, timedelta, time as dt_time


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
    "FINNIFTY": 60,
    "MIDCPNIFTY": 120,
}

# NSE index derivatives expire on Tuesday (1=Tuesday).
EXPIRY_WEEKDAYS = {
    "NIFTY": 1,
    "BANKNIFTY": 1,
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
    if symbol.upper() == "NIFTY":
        days = (weekday - now.weekday()) % 7
        if days == 0 and now.time() > dt_time(15, 30):
            days = 7
        return now + timedelta(days=days)

    expiry = _last_weekday_of_month(now.year, now.month, weekday)
    if expiry.date() < now.date() or (
        expiry.date() == now.date() and now.time() > dt_time(15, 30)
    ):
        next_month = 1 if now.month == 12 else now.month + 1
        next_year = now.year + 1 if now.month == 12 else now.year
        expiry = _last_weekday_of_month(next_year, next_month, weekday)
    return expiry


def get_nearest_expiry(symbol: str) -> str:
    try:
        from jugaad_data.nse import NSELive
        nse = NSELive()
        if symbol.upper() in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
            data = nse.index_option_chain(symbol.upper())
        else:
            data = nse.stock_option_chain(symbol.upper())
            
        expiry_list = data.get("records", {}).get("expiryDates", [])
        if expiry_list:
            nearest_expiry = _parse_expiry_date(expiry_list[0])
            if nearest_expiry:
                return nearest_expiry.strftime("%d %b %Y")
    except Exception:
        pass

    now = datetime.now()
    return _fallback_expiry(symbol, now).strftime("%d %b %Y")

def get_atm_strike(spot_price: float, symbol: str) -> int:
    """Rounds spot price to nearest ATM strike based on index."""
    step = 50  # Default (NIFTY, BANKNIFTY, etc.)
    if "BANK" in symbol.upper():
        step = 100
    return int(round(spot_price / step) * step)

def get_otm_strike(spot_price: float, signal: str, symbol: str) -> int:
    """
    Returns a slightly OTM strike.
    BUY signal → OTM CE (1 strike above ATM)
    SELL signal → OTM PE (1 strike below ATM)
    """
    step = 50
    if "BANK" in symbol.upper():
        step = 100

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


def get_live_nse_option_ltp(
    symbol: str,
    opt_type: str,
    strike: int,
    expiry: str | None = None,
    retries: int = 3,
) -> float | None:
    """
    Fetch live option LTP using jugaad-data to bypass NSE blocking.
    Uses the trade's saved expiry when available, otherwise the nearest expiry.
    Successful quotes are cached briefly to avoid NSE throttling between the
    dashboard's concurrent API calls.
    """
    normalized_symbol = _symbol_key(symbol)
    normalized_type = (opt_type or "").upper()
    target_expiry = _parse_expiry_date(expiry) if expiry else None
    cache_key = (
        normalized_symbol,
        normalized_type,
        int(strike),
        target_expiry.date().isoformat() if target_expiry else None,
    )
    cached_price = _cached_option_ltp(cache_key)
    if cached_price is not None:
        return cached_price

    for attempt in range(retries):
        try:
            from jugaad_data.nse import NSELive
            nse = NSELive()
            
            # Determine if it's index or stock. For DhanScanner, we assume NIFTY/BANKNIFTY are indices.
            if symbol.upper() in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
                data = nse.index_option_chain(symbol.upper())
            else:
                data = nse.stock_option_chain(symbol.upper())
                
            records = data.get("records", {}).get("data", [])
            expiry_list = data.get("records", {}).get("expiryDates", [])
            
            if not expiry_list:
                time.sleep(1)
                continue
                
            selected_expiry = target_expiry or _parse_expiry_date(expiry_list[0])
            if selected_expiry is None:
                time.sleep(1)
                continue
            
            for record in records:
                if record.get("strikePrice") == strike:
                    opt_data = record.get(normalized_type, {})
                    record_expiry = _parse_expiry_date(opt_data.get("expiryDate"))
                    if record_expiry and record_expiry.date() == selected_expiry.date():
                        price = float(opt_data.get("lastPrice", 0.0))
                        if price > 0:
                            _store_option_ltp(cache_key, price)
                            return price
        except Exception as exc:
            if attempt == retries - 1:
                print(f"NSE option LTP unavailable for {symbol} {strike} {normalized_type}: {exc}")
        if attempt < retries - 1:
            time.sleep(1)

    # If NSE is temporarily unavailable after a successful lookup, retain the
    # last known quote rather than returning a misleading blank live P&L.
    return _cached_option_ltp(cache_key)

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
        
    # Attempt "Jugaad" to fetch Live NSE Option LTP first
    premium = get_live_nse_option_ltp(symbol, opt_type, strike)
    
    # Preserve the previous behavior if NSE's option-chain response is unavailable.
    if premium is None or premium <= 0:
        premium = estimate_option_premium(spot_price, strike, signal, days_to_expiry=days)
    
    stop_loss_risk_pct = 0.05 if symbol_key == "NIFTY" else 0.15
    sl = round(premium * (1 - stop_loss_risk_pct), 1)
    t1 = round(premium * 1.80, 1)
    t2 = round(premium * 2.50, 1)

    return {
        "opt_type": opt_type,
        "strike": strike,
        "expiry": expiry,
        "lot_size": lot_size,
        "premium": premium,
        "sl": sl,
        "t1": t1,
        "t2": t2,
    }

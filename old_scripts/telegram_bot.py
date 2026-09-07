import re
from datetime import datetime, time, timedelta

import requests
from config import (
    EXPIRY_DATES,
    EXPIRY_WEEKDAYS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TRADE_ALERT_MIN_CONFIDENCE,
)


INSTRUMENT_DISPLAY_NAMES = {
    "Nifty 50": "NIFTY",
    "BankNifty": "BANKNIFTY",
    "FinNifty": "FINNIFTY",
    "Sensex": "SENSEX",
}

LOT_SIZES = {
    "Nifty 50": 25,
    "BankNifty": 15,
    "FinNifty": 40,
    "Sensex": 10,
}

LAST_TRADE_ALERTS = {}


def _clean_text(value):
    return re.sub(r"[*_`]+", "", str(value)).strip()


def _round_price(value):
    try:
        return str(int(round(float(value))))
    except (TypeError, ValueError):
        return str(value)


def _next_expiry_label(instrument_name, now=None):
    """
    Returns the configured next expiry label for the instrument.
    Exact date env vars are preferred over weekday rules.
    """
    now = now or datetime.now()

    exact_date = EXPIRY_DATES.get(instrument_name)
    if exact_date:
        try:
            expiry = datetime.strptime(exact_date, "%Y-%m-%d")
            return expiry.strftime("%d %B EXPIRY").upper().lstrip("0")
        except ValueError:
            pass

    expiry_weekday = EXPIRY_WEEKDAYS.get(instrument_name, 3)
    days_until_expiry = (expiry_weekday - now.weekday()) % 7
    if days_until_expiry == 0 and now.time() > time(15, 30):
        days_until_expiry = 7

    expiry = now + timedelta(days=days_until_expiry)
    return expiry.strftime("%d %B EXPIRY").upper().lstrip("0")


def _extract_number(label, message):
    match = re.search(rf"{re.escape(label)}\s*:?\s*(?:Rs\.?|INR|₹)?\s*([0-9]+(?:\.[0-9]+)?)", message, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_text(label, message):
    match = re.search(rf"{re.escape(label)}\s*:?\s*([^\n]+)", message, re.IGNORECASE)
    return _clean_text(match.group(1)) if match else None


def _find_instrument(message):
    return next(
        (name for name in INSTRUMENT_DISPLAY_NAMES if name.lower() in message.lower()),
        None,
    )


def _format_rupees(value):
    return f"{int(round(value)):,}"


def _trade_direction(option_type):
    return "Bullish" if option_type.upper() == "CE" else "Bearish"


def _market_context_from_message(message):
    pcr = _extract_number("PCR", message)
    vix = _extract_number("India VIX", message)
    return {
        "price": _extract_number("Price", message),
        "rsi": _extract_number("RSI", message),
        "news_sentiment": _extract_text("News Sentiment", message),
        "global_mood": _extract_text("Global Mood", message),
        "daily_trend": _extract_text("Daily Trend", message),
        "pcr": float(pcr) if pcr else None,
        "vix": float(vix) if vix else None,
        "max_pain": _extract_text("Max Pain", message),
        "resistance": _extract_text("Resistance (Call OI)", message),
        "support": _extract_text("Support (Put OI)", message),
        "has_caution": "CAUTION" in message.upper(),
    }


def _context_alignment(value, direction):
    if not value:
        return "Neutral"

    text = value.lower()
    if direction == "Bullish":
        if "bullish" in text:
            return "Aligned"
        if "bearish" in text:
            return "Against"
    else:
        if "bearish" in text:
            return "Aligned"
        if "bullish" in text:
            return "Against"
    return "Neutral"


def _option_chain_alignment(pcr, direction):
    if pcr is None:
        return "Neutral"
    if direction == "Bullish":
        if pcr > 1.2:
            return "Aligned"
        if pcr < 0.8:
            return "Against"
    else:
        if pcr < 0.8:
            return "Aligned"
        if pcr > 1.2:
            return "Against"
    return "Neutral"


def _build_reasons(message, option_type, context):
    direction = _trade_direction(option_type)
    upper = message.upper()
    reasons = []

    if "HIGH-PROBABILITY" in upper:
        reasons.append(f"{direction} high-probability trend signal")
    if "ABOVE VWAP" in upper:
        reasons.append("Price is trading above VWAP")
    if "BELOW VWAP" in upper:
        reasons.append("Price is trading below VWAP")
    if "5M BULLISH" in upper:
        reasons.append("5-minute trend is bullish")
    if "5M BEARISH" in upper:
        reasons.append("5-minute trend is bearish")
    if "15M" in upper:
        reasons.append("Higher timeframe confirmation present")
    if "MACD" in upper:
        reasons.append("MACD crossover confirmed")
    if "SUPERTREND" in upper:
        reasons.append("Supertrend direction changed")
    if "BOLLINGER SQUEEZE" in upper:
        reasons.append("Bollinger squeeze breakout fired")
    if "INSIDE BAR" in upper:
        reasons.append("Inside-bar breakout confirmed")
    if "RSI" in upper:
        rsi = context.get("rsi")
        suffix = f" ({_round_price(rsi)})" if rsi else ""
        reasons.append(f"RSI check included{suffix}")

    for key, label in (("daily_trend", "Daily trend"), ("news_sentiment", "News sentiment")):
        alignment = _context_alignment(context.get(key), direction)
        if alignment == "Aligned":
            reasons.append(f"{label} supports this side")
        elif alignment == "Against":
            reasons.append(f"{label} is against this trade")

    option_alignment = _option_chain_alignment(context.get("pcr"), direction)
    if option_alignment == "Aligned":
        reasons.append("Option chain supports this side")
    elif option_alignment == "Against":
        reasons.append("Option chain is against this trade")

    return reasons[:6] or [f"{direction} setup detected by scanner"]


def _score_trade(message, option_type, context):
    direction = _trade_direction(option_type)
    upper = message.upper()
    score = 50

    if "HIGH-PROBABILITY" in upper:
        score += 14
    if "SUPERTREND" in upper:
        score += 12
    if "MACD" in upper:
        score += 9
    if "ORB" in upper:
        score += 8
    if "BOLLINGER SQUEEZE" in upper:
        score += 8
    if "INSIDE BAR" in upper:
        score += 7
    if "VWAP" in upper:
        score += 7
    if "5M" in upper:
        score += 6
    if "15M" in upper:
        score += 6

    for key in ("daily_trend", "news_sentiment"):
        alignment = _context_alignment(context.get(key), direction)
        if alignment == "Aligned":
            score += 6
        elif alignment == "Against":
            score -= 8

    option_alignment = _option_chain_alignment(context.get("pcr"), direction)
    if option_alignment == "Aligned":
        score += 8
    elif option_alignment == "Against":
        score -= 10

    vix = context.get("vix")
    if vix and vix > 20:
        score -= 10
    elif vix and vix < 14:
        score += 3

    if "RSI OVERBOUGHT" in upper or "RSI OVERSOLD" in upper:
        score -= 12
    if context.get("has_caution"):
        score -= 12

    return max(35, min(95, int(score)))


def _quality_label(score):
    if score >= 80:
        return "HIGH"
    if score >= 65:
        return "GOOD"
    if score >= 50:
        return "MEDIUM"
    return "LOW / AVOID"


def _risk_note(context, score):
    vix = context.get("vix")
    if score < 50:
        return "Risk: Weak setup. Avoid or use very small quantity."
    if vix and vix > 20:
        return "Risk: High volatility. Reduce quantity and trail SL fast."
    if context.get("has_caution"):
        return "Risk: Macro context conflict. Wait for clean entry."
    return "Risk: Normal. Follow SL strictly."


def _format_target_hit_message(message):
    if "TRAIL SL ALERT" not in message.upper():
        return None

    instrument_name = _find_instrument(message)
    trade = LAST_TRADE_ALERTS.get(instrument_name)
    if not instrument_name or not trade:
        return message

    points = max(0, float(trade["target_1"]) - float(trade["entry"]))
    pnl = points * LOT_SIZES.get(instrument_name, 1) * trade["lots"]
    display_name = INSTRUMENT_DISPLAY_NAMES[instrument_name]

    return "\n".join([
        f"BUY {display_name} {trade['strike']} {trade['option_type']} ABOVE {trade['entry_text']} TARGET {trade['target_text']}",
        "",
        f"{trade['entry_text']} - {trade['target_1_text']} HIT IN {display_name} {trade['option_type']}",
        "",
        f"EASY {int(round(points))}+ POINTS GAINS",
        "",
        "1ST TARGET ACHIEVED",
        "",
        f"GAINING RS {_format_rupees(pnl)}/{trade['lots']} LOT",
        "",
        "BOOK PROFIT OR TRAIL SL",
    ])


def format_paid_index_message(message):
    """
    Converts scanner trade alerts into the compact Telegram format shown by the user.
    Non-trade messages are returned unchanged.
    """
    target_hit_message = _format_target_hit_message(message)
    if target_hit_message:
        return target_hit_message

    hint = re.search(r"\bHint:\s*BUY\s+(\d+)\s+(CE|PE)\b", message, re.IGNORECASE)
    if not hint:
        return message

    instrument_name = _find_instrument(message)
    if not instrument_name:
        return message

    strike, option_type = hint.groups()
    display_name = INSTRUMENT_DISPLAY_NAMES[instrument_name]
    entry = _extract_number("Prem Buy Price", message)
    target_1 = _extract_number("Prem Target 1", message)
    target_2 = _extract_number("Prem Target 2", message)
    stop_loss = _extract_number("Prem Stop Loss", message)

    if not all([entry, target_1, target_2, stop_loss]):
        return message

    entry_text = _round_price(entry)
    target_text = f"{_round_price(target_1)} / {_round_price(target_2)}"
    stop_loss_text = _round_price(stop_loss)
    context = _market_context_from_message(message)
    score = _score_trade(message, option_type, context)
    if score < TRADE_ALERT_MIN_CONFIDENCE:
        print(
            f"SKIPPED Telegram trade alert: confidence {score}% "
            f"is below {TRADE_ALERT_MIN_CONFIDENCE}% threshold."
        )
        return None

    quality = _quality_label(score)
    reasons = _build_reasons(message, option_type, context)
    lots_match = re.search(r"Position\s*:\s*(\d+)\s+Lots?", message, re.IGNORECASE)
    lots = int(lots_match.group(1)) if lots_match else 1

    LAST_TRADE_ALERTS[instrument_name] = {
        "strike": strike,
        "option_type": option_type.upper(),
        "entry": float(entry),
        "entry_text": entry_text,
        "target_1": float(target_1),
        "target_1_text": _round_price(target_1),
        "target_text": target_text,
        "lots": lots,
    }

    lines = [
        f"BUY {display_name} {strike} {option_type.upper()} ABOVE {entry_text}",
        "",
        f"TARGET - {target_text}",
        "",
        f"SL - {stop_loss_text}",
        "",
        _next_expiry_label(instrument_name),
        "",
        f"CONFIDENCE - {score}% ({quality})",
        f"TRADE QUALITY - {quality}",
        "",
        "REASON:",
    ]

    lines.extend(f"- {reason}" for reason in reasons)

    if context.get("price") or context.get("daily_trend") or context.get("pcr"):
        lines.extend(["", "MARKET CHECK:"])
        if context.get("price"):
            lines.append(f"- Spot price: {_round_price(context['price'])}")
        if context.get("daily_trend"):
            lines.append(f"- Daily trend: {context['daily_trend']}")
        if context.get("news_sentiment"):
            lines.append(f"- News: {context['news_sentiment']}")
        if context.get("vix"):
            lines.append(f"- India VIX: {context['vix']}")

    if context.get("pcr") is not None or context.get("max_pain"):
        lines.extend(["", "OPTION CHAIN:"])
        if context.get("pcr") is not None:
            lines.append(f"- PCR: {context['pcr']}")
        if context.get("max_pain"):
            lines.append(f"- Max pain: {context['max_pain']}")
        if context.get("support"):
            lines.append(f"- Support: {context['support']}")
        if context.get("resistance"):
            lines.append(f"- Resistance: {context['resistance']}")

    lines.extend([
        "",
        _risk_note(context, score),
        "",
        "Plss wait for the buying level",
        "",
        f"Buy only above {entry_text}",
    ])

    if score < 50:
        lines.extend(["", "NO TRADE unless price sustains above entry."])

    return "\n".join(lines)


def format_market_context_summary(market_context):
    if not market_context:
        return None

    daily_trends = market_context.get("daily_trends", {})
    bullish = [name for name, trend in daily_trends.items() if trend == "Bullish"]
    bearish = [name for name, trend in daily_trends.items() if trend == "Bearish"]

    best_action = "WAIT for clean breakout"
    if len(bullish) > len(bearish):
        best_action = "Prefer CE only on clean breakout"
    elif len(bearish) > len(bullish):
        best_action = "Prefer PE only on clean breakdown"

    lines = [
        "MARKET AUTO SUMMARY",
        f"Time: {datetime.now().strftime('%H:%M')}",
        "",
        f"News Sentiment: {market_context.get('news_sentiment', 'Neutral')}",
        f"Global Mood: {market_context.get('global_mood', 'Neutral')} ({market_context.get('sp500_change', 0)}%)",
        f"India VIX: {market_context.get('vix', 15.0)}",
        "",
        "DAILY TREND:",
    ]

    for name in INSTRUMENT_DISPLAY_NAMES:
        lines.append(f"- {INSTRUMENT_DISPLAY_NAMES[name]}: {daily_trends.get(name, 'Neutral')}")

    lines.extend([
        "",
        f"Best action: {best_action}",
        "Rule: Trade only after entry level breaks with candle close.",
    ])

    return "\n".join(lines)


def send_telegram_message(message):
    """
    Sends a message to the configured Telegram chat.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Credentials missing. Cannot send message.")
        return False

    message = format_paid_index_message(message)
    if not message:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print(f"SUCCESS: Alert sent to Telegram: {message}")
            return True
        else:
            print(f"FAILED to send Telegram alert: {response.text}")
            return False
    except Exception as e:
        print(f"ERROR sending Telegram alert: {e}")
        return False

# You can run this file directly to test the Telegram bot.
if __name__ == "__main__":
    send_telegram_message("Test Alert: Your Trading Scanner bot is successfully connected!")

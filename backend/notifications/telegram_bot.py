import re
from html import unescape
from typing import Any

import requests


class TelegramNotifier:
    def __init__(self, bot_token: str | None, chat_id: str | None):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def _print_to_terminal(self, text: str) -> None:
        try:
            terminal_text = unescape(re.sub(r"</?[^>]+>", "", text))
            print("\n--- Telegram Message ---")
            try:
                print(terminal_text)
            except UnicodeEncodeError:
                print(terminal_text.encode('ascii', errors='replace').decode('ascii'))
            print("--- End Telegram Message ---\n")
        except Exception:
            pass

    def send_message(self, text: str) -> bool:
        self._print_to_terminal(text)

        if not self.bot_token or not self.chat_id:
            print("Telegram credentials not configured.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                return True

            print(f"Telegram error: {response.text}")
            return False
        except Exception as e:
            print(f"Failed to send telegram message: {e}")
            return False

    def send_trade_alert(
        self,
        signal_data: dict[str, Any],
        options_info: dict[str, Any] | None = None,
    ) -> bool:
        """
        Sends a formatted trade alert with CE/PE recommendation.
        """
        action = signal_data["signal"]

        if options_info:
            scrip_name = f"{signal_data['symbol']} {options_info['strike']} {options_info['opt_type']}"
            trade_type = "Intraday Options"
            entry_p = options_info["premium"]
            entry_str = f"Rs.{entry_p:.1f} - Rs.{entry_p * 1.05:.1f}"
            t1 = options_info["t1"]
            t2 = options_info["t2"]
            sl = options_info["sl"]
        else:
            scrip_name = signal_data["symbol"]
            trade_type = "Intraday Equity"
            entry_p = signal_data["entry"]
            if action == "BUY":
                entry_str = f"Rs.{entry_p:.2f} - Rs.{entry_p * 1.002:.2f}"
            else:
                entry_str = f"Rs.{entry_p * 0.998:.2f} - Rs.{entry_p:.2f}"
            t1 = signal_data["target_1"]
            t2 = signal_data["target_2"]
            sl = signal_data["stop_loss"]

        logic = "AI Multi-Indicator Confluence"
        if "breakdown" in signal_data and isinstance(signal_data["breakdown"], dict):
            triggers = []
            for strat, res in signal_data["breakdown"].items():
                if res.get("signal") == action:
                    triggers.append(strat.replace("Strategy", ""))
            if triggers[:2]:
                logic = " + ".join(triggers[:2]) + " pattern"

        msg = f"<b>[{action}] {scrip_name}</b>\n"
        msg += f"<b>Type:</b> {trade_type}\n"
        msg += f"<b>Entry:</b> {entry_str}\n"
        msg += f"<b>Target 1:</b> Rs.{t1:.2f}\n"
        msg += f"<b>Target 2:</b> Rs.{t2:.2f}\n"
        msg += f"<b>Stop Loss:</b> Rs.{sl:.2f} (Strict)\n"
        msg += f"<b>Logic:</b> {logic}\n"
        msg += f"<b>Risk:</b> Use only 5% Capital | RR: {signal_data.get('risk_reward', '1:3')}\n\n"
        msg += f"<i>Confidence: {signal_data.get('confidence', 0)}%</i>\n"
        msg += f"#{signal_data['symbol']} #TradingBot"

        return self.send_message(msg)

    def send_update_alert(
        self,
        symbol: str,
        target_hit: int,
        target_price: float,
        current_price: float,
        action: str,
    ) -> bool:
        """
        Sends an update alert, such as target hit or trailing SL.
        """
        msg = f"<b>UPDATE: {symbol}</b>\n"
        msg += f"Target {target_hit} Hit (Rs.{target_price:.2f})\n"
        msg += f"Current Price: Rs.{current_price:.2f}\n"
        msg += f"<b>Action:</b> {action}\n"

        return self.send_message(msg)

    def send_trailing_sl_alert(
        self,
        symbol: str,
        previous_sl: float,
        new_sl: float,
        current_price: float,
    ) -> bool:
        """Send a notification when an open trade's trailing stop is improved."""
        direction = "UP" if new_sl > previous_sl else "DOWN"
        msg = f"<b>TRAILING SL: {symbol}</b>\n"
        msg += f"Current Price: Rs.{current_price:.2f}\n"
        msg += f"<b>SL Moved {direction}:</b> Rs.{previous_sl:.2f} → Rs.{new_sl:.2f}"

        return self.send_message(msg)

    def send_exit_alert(
        self,
        symbol: str,
        reason: str,
        exit_price: float,
        is_loss: bool = True,
    ) -> bool:
        """
        Sends an exit alert, such as SL hit, square-off, or full exit.
        """
        if "SL" in reason.upper() or "STOP" in reason.upper():
            msg = f"<b>EXIT: {symbol}</b>\n"
            msg += f"Stop Loss Hit at Rs.{exit_price:.2f}.\n"
            msg += "Move to the next trade. Follow proper risk management.\n"
        elif is_loss:
            msg = f"<b>EXIT: {symbol}</b>\n"
            msg += f"{reason}.\n"
            msg += f"Exit full quantity at Rs.{exit_price:.2f}.\n"
        else:
            msg = f"<b>FULL EXIT: {symbol}</b>\n"
            msg += f"{reason}.\n"
            msg += f"Exit full quantity at Rs.{exit_price:.2f}.\n"

        return self.send_message(msg)

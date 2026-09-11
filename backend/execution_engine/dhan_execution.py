import os
import time
import uuid
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

from ..models import Trade
from ..trade_metadata import parse_trade_metadata, serialize_trade_metadata


class DhanExecutionEngine:
    """Places and tracks Dhan orders when live trading is explicitly enabled."""

    ORDER_URL = "https://api.dhan.co/v2/orders"
    SUPER_ORDER_URL = "https://api.dhan.co/v2/super/orders"
    POSITIONS_URL = "https://api.dhan.co/v2/positions"

    def __init__(self, db_session: Session):
        self.db = db_session
        self.client_id = os.getenv("DHAN_CLIENT_ID")
        self.access_token = os.getenv("DHAN_ACCESS_TOKEN")
        self.live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

    def _assert_ready(self) -> None:
        if not self.live_enabled:
            raise RuntimeError("Live trading is disabled; set LIVE_TRADING_ENABLED=true to enable it")
        if not self.client_id or not self.access_token:
            raise RuntimeError("DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN are required for live trading")
        if self.client_id.upper() in {"DUMMY", "YOUR_CLIENT_ID", "REPLACE_ME"}:
            raise RuntimeError("Dummy Dhan credentials cannot place live orders")

    def _order_headers(self) -> dict:
        self._assert_ready()
        if self.access_token is None:
            raise RuntimeError("DHAN_ACCESS_TOKEN is required for live trading")
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Forwarded-For": "152.59.175.114",
            "X-Real-IP": "152.59.175.114",
        }

    def _request_order(self, payload: dict) -> dict:
        self._assert_ready()
        response = requests.post(
            self.ORDER_URL,
            headers=self._order_headers(),
            json=payload,
            timeout=(3.05, 10),
        )
        try:
            result = response.json()
        except Exception:
            result = {}
        if not response.ok:
            error_msg = result.get("errorMessage") or result.get("remarks") or result.get("errorType") or f"HTTP {response.status_code}"
            error_code = result.get("errorCode", "")
            raise RuntimeError(f"Dhan error [{error_code}]: {error_msg}".strip())
        if not result.get("orderId"):
            error_msg = result.get("errorMessage") or result.get("remarks") or str(result)
            raise RuntimeError(f"Dhan did not return an orderId: {error_msg}")

        order_id = result["orderId"]
        status = result.get("orderStatus", "TRANSIT")
        deadline = time.monotonic() + 10
        while status in {"TRANSIT", "PENDING"} and time.monotonic() < deadline:
            time.sleep(0.5)
            result = self._get_order(order_id)
            status = result.get("orderStatus", status)

        if status not in {"TRADED", "PART_TRADED"}:
            if status in {"TRANSIT", "PENDING"}:
                self._cancel_order(order_id)
            reason = result.get("remarks") or result.get("failureReason") or result.get("errorMessage") or status
            raise RuntimeError(f"Dhan order {order_id} was not filled: {status} ({reason})")

        filled_quantity = int(result.get("filledQty") or 0)
        if filled_quantity <= 0:
            raise RuntimeError(f"Dhan order {order_id} has no filled quantity: {result}")
        return result

    def _get_order(self, order_id: str) -> dict:
        response = requests.get(
            f"{self.ORDER_URL}/{order_id}",
            headers=self._order_headers(),
            timeout=(3.05, 10),
        )
        try:
            result = response.json()
        except Exception:
            result = {}
        if not response.ok:
            error_msg = result.get("errorMessage") or result.get("remarks") or f"HTTP {response.status_code}"
            error_code = result.get("errorCode", "")
            raise RuntimeError(f"Dhan error [{error_code}]: {error_msg}".strip())
        return result

    def _cancel_order(self, order_id: str) -> None:
        requests.delete(
            f"{self.ORDER_URL}/{order_id}",
            headers=self._order_headers(),
            timeout=(3.05, 10),
        )

    def _get_super_orders(self) -> list[dict]:
        response = requests.get(self.SUPER_ORDER_URL, headers=self._order_headers(), timeout=(3.05, 10))
        try:
            result = response.json()
        except Exception:
            result = []
        if not response.ok:
            return []
        return result if isinstance(result, list) else []

    def _request_super_order(self, payload: dict) -> dict:
        self._assert_ready()
        response = requests.post(self.SUPER_ORDER_URL, headers=self._order_headers(), json=payload, timeout=(3.05, 10))
        try:
            result = response.json()
        except Exception:
            result = {}
        if not response.ok:
            error_msg = result.get("errorMessage") or result.get("remarks") or result.get("errorType") or f"HTTP {response.status_code}"
            error_code = result.get("errorCode", "")
            raise RuntimeError(f"Dhan error [{error_code}]: {error_msg}".strip())
        order_id = result.get("orderId")
        if not order_id:
            error_msg = result.get("errorMessage") or result.get("remarks") or str(result)
            raise RuntimeError(f"Dhan did not return a super orderId: {error_msg}")
        status = result.get("orderStatus", "TRANSIT")
        deadline = time.monotonic() + 15
        while status in {"TRANSIT", "PENDING", "PART_TRADED"} and time.monotonic() < deadline:
            time.sleep(0.5)
            current = next((item for item in self._get_super_orders() if item.get("orderId") == order_id), None)
            if current:
                result, status = current, current.get("orderStatus", status)
        if status != "TRADED" or int(result.get("filledQty") or 0) <= 0:
            reason = result.get("remarks") or result.get("failureReason") or result.get("errorMessage") or status
            raise RuntimeError(f"Dhan super order {order_id} was not fully filled: {status} ({reason})")
        return result

    def _cancel_super_leg(self, order_id: str, leg_name: str) -> None:
        requests.delete(f"{self.SUPER_ORDER_URL}/{order_id}/{leg_name}", headers=self._order_headers(), timeout=(3.05, 10))

    def get_positions(self) -> list[dict]:
        self._assert_ready()
        response = requests.get(self.POSITIONS_URL, headers=self._order_headers(), timeout=(3.05, 10))
        response.raise_for_status()
        return response.json()

    def reconcile_trade(self, trade: Trade) -> bool:
        metadata = parse_trade_metadata(trade.notes)
        security_id = str(metadata.get("security_id", ""))
        positions = self.get_positions()
        position = next((item for item in positions if str(item.get("securityId")) == security_id), None)
        return bool(position and int(position.get("netQty") or 0) == int(trade.quantity))

    def place_super_order(self, symbol: str, action: str, quantity: int, price: float, strategy: str, sl: float, t1: float, t2: float, notes: str | None = None) -> Trade:
        self._assert_ready()
        if self.client_id is None:
            raise RuntimeError("DHAN_CLIENT_ID is required for live trading")
        client_id = self.client_id
        metadata = parse_trade_metadata(notes)
        security_id = metadata.get("security_id")
        if not security_id:
            raise RuntimeError("Option security_id is required before placing a Dhan super order")
        if sl is None or t1 is None or t2 is None:
            raise RuntimeError("Stop-loss and target prices are required for a Dhan super order")
        exchange_segment = "BSE_FNO" if symbol.upper() == "SENSEX" else "NSE_FNO"
        result = self._request_super_order({
            "dhanClientId": client_id, "correlationId": f"scanner-{uuid.uuid4().hex[:20]}",
            "transactionType": "BUY" if action.upper() == "BUY" else "SELL", "exchangeSegment": exchange_segment,
            "productType": "INTRADAY", "orderType": "MARKET", "securityId": str(security_id),
            "quantity": int(quantity), "price": 0, "targetPrice": float(t2),
            "stopLossPrice": float(sl), "trailingJump": 0,
        })
        filled_quantity = int(result["filledQty"])
        if filled_quantity != int(quantity):
            raise RuntimeError(f"Dhan super order {result['orderId']} partially filled: {filled_quantity}/{quantity}")
        metadata["dhan_super_order_id"] = result["orderId"]
        metadata["dhan_order_status"] = result.get("orderStatus", "TRADED")
        trade = Trade(symbol=symbol, trade_type=action, entry_price=float(result.get("averageTradedPrice") or price), quantity=filled_quantity, strategy_used=strategy, stop_loss=sl, target_1=t1, target_2=t2, current_sl=sl, highest_target_hit=0, status="OPEN", notes=serialize_trade_metadata(metadata))
        self.db.add(trade); self.db.commit(); self.db.refresh(trade)
        return trade

    def place_market_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        strategy: str,
        sl: float = None,
        t1: float = None,
        t2: float = None,
        notes: str = None,
    ) -> Trade:
        self._assert_ready()
        if self.client_id is None:
            raise RuntimeError("DHAN_CLIENT_ID is required for live trading")
        client_id = self.client_id
        metadata = parse_trade_metadata(notes)
        security_id = metadata.get("security_id")
        if not security_id:
            raise RuntimeError("Option security_id is required before placing a Dhan order")
        trade_sl = sl if sl is not None else price
        trade_t1 = t1 if t1 is not None else price
        trade_t2 = t2 if t2 is not None else price
        trade_notes = notes or ""

        transaction_type = "BUY" if action.upper() == "BUY" else "SELL"
        exchange_segment = "BSE_FNO" if symbol.upper() == "SENSEX" else "NSE_FNO"
        result = self._request_order(
            {
                "dhanClientId": client_id,
                "correlationId": f"scanner-{uuid.uuid4().hex[:20]}",
                "transactionType": transaction_type,
                "exchangeSegment": exchange_segment,
                "productType": "INTRADAY",
                "orderType": "MARKET",
                "validity": "DAY",
                "securityId": str(security_id),
                "quantity": int(quantity),
                "disclosedQuantity": 0,
                "price": 0,
                "triggerPrice": 0,
                "afterMarketOrder": False,
            }
        )

        metadata = parse_trade_metadata(trade_notes)
        metadata["dhan_order_id"] = result["orderId"]
        metadata["dhan_order_status"] = result.get("orderStatus", "TRANSIT")
        filled_quantity = int(result["filledQty"])
        if filled_quantity != int(quantity):
            raise RuntimeError(
                f"Dhan entry order {result['orderId']} partially filled: "
                f"{filled_quantity}/{quantity}; reconcile broker position manually"
            )
        entry_price = float(result.get("averageTradedPrice") or price)
        trade = Trade(
            symbol=symbol,
            trade_type=action,
            entry_price=entry_price,
            quantity=filled_quantity,
            strategy_used=strategy,
            stop_loss=trade_sl,
            target_1=trade_t1,
            target_2=trade_t2,
            current_sl=trade_sl,
            highest_target_hit=0,
            status="OPEN",
            notes=serialize_trade_metadata(metadata),
        )
        self.db.add(trade)
        self.db.commit()
        self.db.refresh(trade)
        return trade

    def close_position(self, trade_id: int, price: float | None, reason: str = "") -> Trade | None:
        trade = self.db.query(Trade).filter(Trade.id == trade_id).first()
        if not trade or trade.status == "CLOSED":
            return None

        self._assert_ready()
        if self.client_id is None:
            raise RuntimeError("DHAN_CLIENT_ID is required for live trading")
        client_id = self.client_id
        metadata = parse_trade_metadata(trade.notes)
        security_id = metadata.get("security_id")
        if not security_id:
            raise RuntimeError("Option security_id is required before closing a Dhan position")
        exchange_segment = "BSE_FNO" if trade.symbol.upper() == "SENSEX" else "NSE_FNO"
        super_order_id = metadata.get("dhan_super_order_id")
        if super_order_id:
            self._cancel_super_leg(super_order_id, "TARGET_LEG")
            self._cancel_super_leg(super_order_id, "STOP_LOSS_LEG")
        result = self._request_order(
            {
                "dhanClientId": client_id,
                "correlationId": f"scanner-exit-{uuid.uuid4().hex[:16]}",
                "transactionType": "SELL" if trade.trade_type == "BUY" else "BUY",
                "exchangeSegment": exchange_segment,
                "productType": "INTRADAY",
                "orderType": "MARKET",
                "validity": "DAY",
                "securityId": str(security_id),
                "quantity": int(trade.quantity),
                "disclosedQuantity": 0,
                "price": 0,
                "triggerPrice": 0,
                "afterMarketOrder": False,
            }
        )

        filled_quantity = int(result["filledQty"])
        if filled_quantity != int(trade.quantity):
            raise RuntimeError(
                f"Dhan exit order partially filled: {filled_quantity}/{trade.quantity}; "
                "reconcile broker position manually"
            )

        realized_exit_price = float(
            result.get("averageTradedPrice") or price or trade.entry_price
        )
        trade.exit_price = realized_exit_price
        trade.exit_time = datetime.now(timezone.utc).replace(tzinfo=None)
        trade.status = "CLOSED"
        if reason:
            metadata["close_reason"] = reason
        trade.notes = serialize_trade_metadata(metadata)
        pnl_per_unit = (
            realized_exit_price - trade.entry_price
            if trade.trade_type == "BUY"
            else trade.entry_price - realized_exit_price
        )
        trade.profit_loss = pnl_per_unit * trade.quantity
        self.db.commit()
        self.db.refresh(trade)
        return trade
from sqlalchemy.orm import Session
from ..models import Trade
from ..trade_metadata import parse_trade_metadata, serialize_trade_metadata
from datetime import datetime, timezone

class PaperExecutionEngine:
    def __init__(self, db_session: Session):
        self.db = db_session

    def place_market_order(self, symbol: str, action: str, quantity: int, price: float, strategy: str, sl: float = None, t1: float = None, t2: float = None, notes: str = None) -> Trade:
        """
        Simulates a market order fill.
        """
        trade = Trade(
            symbol=symbol,
            trade_type=action,
            entry_price=price,
            quantity=quantity,
            strategy_used=strategy,
            stop_loss=sl,
            target_1=t1,
            target_2=t2,
            current_sl=sl,
            highest_target_hit=0,
            status="OPEN",
            notes=notes
        )
        self.db.add(trade)
        self.db.commit()
        self.db.refresh(trade)
        return trade

    def update_trade_state(self, trade_id: int, new_sl: float, target_hit: int) -> Trade:
        """
        Updates the trailing SL and highest target hit for an open trade.
        """
        trade = self.db.query(Trade).filter(Trade.id == trade_id).first()
        if not trade or trade.status == "CLOSED":
            return None
            
        trade.current_sl = new_sl
        if target_hit > trade.highest_target_hit:
            trade.highest_target_hit = target_hit
            
        self.db.commit()
        self.db.refresh(trade)
        return trade

    def close_position(self, trade_id: int, price: float, reason: str = "") -> Trade:
        """
        Simulates closing an open position.
        """
        trade = self.db.query(Trade).filter(Trade.id == trade_id).first()
        if not trade or trade.status == "CLOSED":
            return None
            
        trade.exit_price = price
        trade.exit_time = datetime.now(timezone.utc).replace(tzinfo=None)
        trade.status = "CLOSED"

        metadata = parse_trade_metadata(trade.notes)
        if reason:
            metadata["close_reason"] = reason
        trade.notes = serialize_trade_metadata(metadata) if metadata else None
        
        # Calculate PnL
        if trade.trade_type == "BUY":
            pnl_per_unit = trade.exit_price - trade.entry_price
        else: # SELL
            pnl_per_unit = trade.entry_price - trade.exit_price
            
        trade.profit_loss = pnl_per_unit * trade.quantity
        
        self.db.commit()
        self.db.refresh(trade)
        return trade

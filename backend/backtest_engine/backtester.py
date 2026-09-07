import pandas as pd
import os
from datetime import datetime
from backend.strategy_engine.ai_signal_engine import AISignalEngine

class BacktestEngine:
    def __init__(self, data_file: str, initial_capital: float = 100000.0, risk_pct: float = 1.0):
        self.data_file = data_file
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.risk_pct = risk_pct
        
        self.ai_engine = AISignalEngine()
        self.trades = []
        self.open_trade = None
        self.equity_curve = []
        
    def run(self):
        if not os.path.exists(self.data_file):
            return {"error": "Data file not found"}
            
        df = pd.read_parquet(self.data_file)
        if len(df) < 50:
            return {"error": "Not enough data"}
            
        print(f"Starting backtest on {len(df)} candles...")
        
        # Simulate time streaming
        for i in range(50, len(df)):
            window = df.iloc[:i+1]
            curr_candle = window.iloc[-1]
            
            # Check open trade exits
            if self.open_trade:
                trade = self.open_trade
                is_buy = trade['type'] == "BUY"
                
                # Check Target or SL hit
                exit_price = None
                reason = ""
                
                if is_buy:
                    if curr_candle['high'] >= trade['t1']:
                        exit_price = trade['t1']
                        reason = "Target Hit"
                    elif curr_candle['low'] <= trade['sl']:
                        exit_price = trade['sl']
                        reason = "SL Hit"
                else: # SELL
                    if curr_candle['low'] <= trade['t1']:
                        exit_price = trade['t1']
                        reason = "Target Hit"
                    elif curr_candle['high'] >= trade['sl']:
                        exit_price = trade['sl']
                        reason = "SL Hit"
                        
                if exit_price:
                    # Close trade
                    pnl_per_unit = (exit_price - trade['entry']) if is_buy else (trade['entry'] - exit_price)
                    pnl = pnl_per_unit * trade['qty']
                    
                    self.current_capital += pnl
                    trade['exit_price'] = exit_price
                    trade['exit_time'] = curr_candle['datetime']
                    trade['pnl'] = pnl
                    trade['reason'] = reason
                    
                    self.trades.append(trade)
                    self.equity_curve.append({
                        "time": curr_candle['datetime'],
                        "equity": self.current_capital
                    })
                    self.open_trade = None
                    continue # Wait for next candle before opening new trade
                    
            # Check for new entry if no open trade
            if not self.open_trade:
                signal_data = self.ai_engine.generate_signal(window, "BACKTEST_SYMBOL")
                if signal_data["action"] == "EXECUTE":
                    # Calculate position size
                    risk_amount = self.current_capital * (self.risk_pct / 100)
                    risk_per_unit = abs(signal_data["entry"] - signal_data["stop_loss"])
                    
                    if risk_per_unit > 0:
                        qty = int(risk_amount / risk_per_unit)
                        
                        self.open_trade = {
                            "type": signal_data["signal"],
                            "entry": signal_data["entry"],
                            "qty": qty,
                            "sl": signal_data["stop_loss"],
                            "t1": signal_data["target_1"],
                            "entry_time": curr_candle['datetime']
                        }

        return self.generate_report()
        
    def generate_report(self) -> dict:
        total_trades = len(self.trades)
        if total_trades == 0:
            return {"status": "No trades executed"}
            
        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        losing_trades = [t for t in self.trades if t['pnl'] <= 0]
        
        gross_profit = sum(t['pnl'] for t in winning_trades)
        gross_loss = abs(sum(t['pnl'] for t in losing_trades))
        
        return {
            "initial_capital": self.initial_capital,
            "final_capital": self.current_capital,
            "net_profit": self.current_capital - self.initial_capital,
            "total_trades": total_trades,
            "win_rate": (len(winning_trades) / total_trades) * 100,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float('inf'),
            "best_trade": max(self.trades, key=lambda x: x['pnl'])['pnl'] if total_trades > 0 else 0,
            "worst_trade": min(self.trades, key=lambda x: x['pnl'])['pnl'] if total_trades > 0 else 0,
            "equity_curve": self.equity_curve
        }

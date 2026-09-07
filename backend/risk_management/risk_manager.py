class RiskManager:
    def __init__(
        self,
        total_capital: float = 100000.0,
        risk_per_trade_pct: float = 1.0,
        daily_loss_limit: float = 5000.0,
        daily_profit_target: float = 10000.0,
        max_open_trades: int = 3,
    ):
        self.total_capital = total_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.daily_loss_limit = daily_loss_limit
        self.daily_profit_target = daily_profit_target
        self.max_open_trades = max_open_trades

        self.current_daily_pnl = 0.0
        self.open_trades_count = 0
        self.open_risk = 0.0
        self.trading_halted = False

    def sync_state(
        self,
        daily_pnl: float,
        open_trades_count: int,
        open_risk: float = 0.0,
    ) -> None:
        """Refresh risk state from the database, which is the source of truth."""
        self.current_daily_pnl = float(daily_pnl or 0.0)
        self.open_trades_count = int(open_trades_count or 0)
        self.open_risk = max(0.0, float(open_risk or 0.0))
        self.trading_halted = (
            self.current_daily_pnl <= -self.daily_loss_limit
            or self.current_daily_pnl >= self.daily_profit_target
        )

    def check_trade_allowed(
        self,
        replacing_open_trade: bool = False,
        additional_risk: float = 0.0,
    ) -> dict:
        if self.current_daily_pnl <= -self.daily_loss_limit:
            self.trading_halted = True
            return {
                "allowed": False,
                "reason": f"Daily loss limit (-Rs {self.daily_loss_limit:.2f}) reached.",
            }

        if self.current_daily_pnl >= self.daily_profit_target:
            self.trading_halted = True
            return {
                "allowed": False,
                "reason": f"Daily profit target (+Rs {self.daily_profit_target:.2f}) reached.",
            }

        if self.trading_halted:
            return {"allowed": False, "reason": "Trading halted for the day."}

        projected_open_count = self.open_trades_count + (0 if replacing_open_trade else 1)
        if projected_open_count > self.max_open_trades:
            return {
                "allowed": False,
                "reason": f"Max open trades ({self.max_open_trades}) reached.",
            }

        projected_worst_case_pnl = (
            self.current_daily_pnl
            - self.open_risk
            - max(0.0, float(additional_risk or 0.0))
        )
        if projected_worst_case_pnl <= -self.daily_loss_limit:
            return {
                "allowed": False,
                "reason": (
                    "Daily risk budget exceeded. "
                    f"Open risk Rs {self.open_risk:.2f}, "
                    f"new risk Rs {additional_risk:.2f}, "
                    f"daily limit Rs {self.daily_loss_limit:.2f}."
                ),
            }

        return {"allowed": True, "reason": ""}

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        lot_size: int = 1,
    ) -> dict:
        risk_amount = self.total_capital * (self.risk_per_trade_pct / 100)
        risk_per_unit = abs(entry_price - stop_loss)

        if entry_price <= 0 or risk_per_unit <= 0:
            return {
                "qty": 0,
                "risk_amount": risk_amount,
                "capital_required": 0.0,
                "error": "Invalid entry price or stop loss",
            }

        lot_size = max(1, int(lot_size))
        risk_limited_qty = int(risk_amount / risk_per_unit)
        capital_limited_qty = int(self.total_capital / entry_price)
        qty = min(risk_limited_qty, capital_limited_qty)
        qty = (qty // lot_size) * lot_size

        if qty <= 0:
            minimum_lot_risk = risk_per_unit * lot_size
            return {
                "qty": 0,
                "risk_amount": risk_amount,
                "capital_required": 0.0,
                "max_loss": 0.0,
                "minimum_lot_risk": minimum_lot_risk,
                "error": (
                    "Capital or risk limit is too small for one lot. "
                    f"Minimum lot risk is Rs {minimum_lot_risk:.2f}, "
                    f"configured risk is Rs {risk_amount:.2f}."
                ),
            }

        return {
            "qty": qty,
            "risk_amount": risk_amount,
            "capital_required": qty * entry_price,
            "max_loss": qty * risk_per_unit,
        }

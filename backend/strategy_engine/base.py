from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    """
    def __init__(self, name: str):
        self.name = name
        self.is_enabled = True

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> dict:
        """
        Evaluate the strategy on the given DataFrame.
        Returns a dictionary with the signal details.
        Expected format:
        {
            "signal": "BUY" | "SELL" | "NEUTRAL",
            "confidence": 0-100, # Strategy specific confidence
            "details": {} # Any extra context
        }
        """
        pass

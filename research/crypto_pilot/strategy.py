"""Strategy rules and signal generation for fixed SMA 20/50 long/cash spot pilot."""

from typing import List, Optional, Tuple

from .models import Candle, Position


class SMA20_50Strategy:
    """
    Fixed SMA 20 / SMA 50 trend-following rule.
    
    Rule specification:
    - Fast window: 20 completed daily closes.
    - Slow window: 50 completed daily closes.
    - If observed history < 50 days: Target position is CASH.
    - If SMA(20) > SMA(50): Target position is LONG.
    - If SMA(20) <= SMA(50): Target position is CASH.
    
    No Lookahead Guarantee:
    - Signal for day t is evaluated strictly at the close of day t using completed closes [0..t].
    - It is never evaluated using day t+1 prices.
    - Orders generated from day t signal are filled at day t+1 Open.
    """

    def __init__(self, fast_window: int = 20, slow_window: int = 50) -> None:
        if fast_window <= 0 or slow_window <= 0:
            raise ValueError("SMA windows must be positive integers")
        if fast_window >= slow_window:
            raise ValueError(f"fast_window ({fast_window}) must be strictly less than slow_window ({slow_window})")
        self.fast_window = fast_window
        self.slow_window = slow_window

    def compute_indicators(self, close_prices_history: List[float]) -> Tuple[Optional[float], Optional[float]]:
        """
        Compute SMA fast and SMA slow from historical close prices up to current bar t.
        History length must represent only completed bars up to t.
        """
        n = len(close_prices_history)
        sma_fast: Optional[float] = None
        sma_slow: Optional[float] = None

        if n >= self.fast_window:
            sma_fast = sum(close_prices_history[-self.fast_window :]) / float(self.fast_window)

        if n >= self.slow_window:
            sma_slow = sum(close_prices_history[-self.slow_window :]) / float(self.slow_window)

        return sma_fast, sma_slow

    def decide_target_position(
        self, close_prices_history: List[float]
    ) -> Tuple[Position, Optional[float], Optional[float]]:
        """
        Evaluate target position based on close prices up to and including day t.
        Returns: (target_position, sma_fast, sma_slow)
        """
        sma_fast, sma_slow = self.compute_indicators(close_prices_history)
        if sma_fast is not None and sma_slow is not None:
            if sma_fast > sma_slow:
                return Position.LONG, sma_fast, sma_slow
            else:
                return Position.CASH, sma_fast, sma_slow
        return Position.CASH, sma_fast, sma_slow

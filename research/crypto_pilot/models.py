"""Data models and value objects for the crypto research pilot backtester."""

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import List, Optional


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Position(str, Enum):
    CASH = "CASH"
    LONG = "LONG"


class ValidationError(Exception):
    """Raised when daily OHLC data violates validation rules."""
    pass


@dataclass(frozen=True)
class Candle:
    """Daily OHLCV candle with strict validation attributes."""
    time: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if not isinstance(self.time, date):
            raise ValidationError(f"Candle time must be a datetime.date instance, got {type(self.time)}")
        for name in ('open', 'high', 'low', 'close', 'volume'):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValidationError(f'Date {self.time}: {name} must be finite')
        if self.open <= 0:
            raise ValidationError(f"Date {self.time}: open price must be > 0, got {self.open}")
        if self.high <= 0:
            raise ValidationError(f"Date {self.time}: high price must be > 0, got {self.high}")
        if self.low <= 0:
            raise ValidationError(f"Date {self.time}: low price must be > 0, got {self.low}")
        if self.close <= 0:
            raise ValidationError(f"Date {self.time}: close price must be > 0, got {self.close}")
        if self.volume < 0:
            raise ValidationError(f"Date {self.time}: volume cannot be negative, got {self.volume}")
        if self.high < self.low:
            raise ValidationError(f"Date {self.time}: impossible candle - high ({self.high}) < low ({self.low})")
        if self.open < self.low or self.open > self.high:
            raise ValidationError(
                f"Date {self.time}: impossible candle - open ({self.open}) outside [low={self.low}, high={self.high}]"
            )
        if self.close < self.low or self.close > self.high:
            raise ValidationError(
                f"Date {self.time}: impossible candle - close ({self.close}) outside [low={self.low}, high={self.high}]"
            )


def validate_candles_sequence(candles: List[Candle]) -> None:
    """
    Strict validation of a list of candles:
    - Must be non-empty list of Candle instances.
    - Strictly increasing dates (no out-of-order dates).
    - No duplicate daily dates.
    - No daily date gaps > 1 day.
    - Each candle re-checked for structural sanity.
    """
    if not candles:
        raise ValidationError("Candle sequence cannot be empty")

    prev_date: Optional[date] = None
    for idx, c in enumerate(candles):
        if not isinstance(c, Candle):
            raise ValidationError(f"Index {idx}: expected Candle instance, got {type(c)}")

        # Structural sanity check
        if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
            raise ValidationError(f"Date {c.time}: nonpositive price detected in candle sequence")
        if c.high < c.low:
            raise ValidationError(f"Date {c.time}: high ({c.high}) < low ({c.low})")
        if c.open < c.low or c.open > c.high:
            raise ValidationError(f"Date {c.time}: open ({c.open}) outside [low={c.low}, high={c.high}]")
        if c.close < c.low or c.close > c.high:
            raise ValidationError(f"Date {c.time}: close ({c.close}) outside [low={c.low}, high={c.high}]")
        if c.volume < 0:
            raise ValidationError(f"Date {c.time}: negative volume ({c.volume})")

        # Chronological monotonicity & gap checks
        if prev_date is not None:
            if c.time == prev_date:
                raise ValidationError(f"Index {idx}: duplicate daily date {c.time}")
            if c.time < prev_date:
                raise ValidationError(f"Index {idx}: out-of-order date {c.time} is before previous date {prev_date}")
            gap = (c.time - prev_date).days
            if gap > 1:
                raise ValidationError(
                    f"Index {idx}: missing daily date gap between {prev_date} and {c.time} ({gap} days)"
                )
        prev_date = c.time


@dataclass(frozen=True)
class Fill:
    """Execution record for an actual transaction."""
    date: date
    side: OrderSide
    base_price: float
    effective_price: float
    units: float
    gross_value: float
    fee: float
    net_cash_flow: float
    resulting_cash: float
    resulting_units: float
    reason: str


@dataclass(frozen=True)
class DailyRecord:
    """End-of-day portfolio and indicator snapshot."""
    date: date
    close: float
    sma_fast: Optional[float]
    sma_slow: Optional[float]
    target_signal: Position
    position: Position
    cash: float
    units: float
    equity: float
    peak_equity: float
    drawdown: float


@dataclass(frozen=True)
class PerformanceMetrics:
    """Summary metrics of a backtest evaluation period."""
    period_name: str
    start_date: date
    end_date: date
    total_days: int
    initial_cash: float
    final_equity: float
    cumulative_return: float
    max_drawdown: float
    num_fills: int
    total_fees_paid: float
    position_at_start: Position = Position.CASH


@dataclass
class BacktestResult:
    """Complete result container for strategy and comparator backtest."""
    strategy_metrics: PerformanceMetrics
    buy_and_hold_metrics: PerformanceMetrics
    in_sample_metrics: Optional[PerformanceMetrics] = None
    held_out_metrics: Optional[PerformanceMetrics] = None
    in_sample_buy_and_hold_metrics: Optional[PerformanceMetrics] = None
    held_out_buy_and_hold_metrics: Optional[PerformanceMetrics] = None
    fills: List[Fill] = field(default_factory=list)
    daily_records: List[DailyRecord] = field(default_factory=list)
    buy_and_hold_fills: List[Fill] = field(default_factory=list)

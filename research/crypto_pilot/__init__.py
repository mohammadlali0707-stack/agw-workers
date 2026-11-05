"""
Crypto Research-Only Pilot Package.

Deterministic long/cash spot historical backtesting system with exact accounting,
strict data validation, lookahead prevention, and no claims of profitable edge.
"""

from .data_loader import load_candles_from_csv
from .engine import BacktestEngine
from .metrics import calculate_metrics
from .models import (
    BacktestResult,
    Candle,
    DailyRecord,
    Fill,
    OrderSide,
    PerformanceMetrics,
    Position,
    ValidationError,
)
from .strategy import SMA20_50Strategy

__all__ = [
    "load_candles_from_csv",
    "BacktestEngine",
    "calculate_metrics",
    "BacktestResult",
    "Candle",
    "DailyRecord",
    "Fill",
    "OrderSide",
    "PerformanceMetrics",
    "Position",
    "ValidationError",
    "SMA20_50Strategy",
]

"""Performance metrics computation for crypto pilot backtest."""

from datetime import date
from typing import List, Optional

from .models import DailyRecord, Fill, PerformanceMetrics, Position


def calculate_metrics(
    period_name: str,
    daily_records: List[DailyRecord],
    fills: List[Fill],
    initial_cash: float,
    position_at_start: Position = Position.CASH,
) -> PerformanceMetrics:
    """
    Calculate summary performance metrics for a series of daily records and fills.
    """
    if not daily_records:
        raise ValueError("Cannot calculate metrics on empty daily records")

    start_date = daily_records[0].date
    end_date = daily_records[-1].date
    total_days = len(daily_records)

    start_equity = initial_cash
    final_equity = daily_records[-1].equity

    cumulative_return = (final_equity - start_equity) / start_equity if start_equity > 0 else 0.0

    # Max drawdown within this window
    peak = start_equity
    max_dd = 0.0
    for record in daily_records:
        if record.equity > peak:
            peak = record.equity
        if peak > 0:
            dd = (peak - record.equity) / peak
            if dd > max_dd:
                max_dd = dd

    # Filter fills strictly within [start_date, end_date]
    window_fills = [f for f in fills if start_date <= f.date <= end_date]
    total_fees = sum(f.fee for f in window_fills)

    return PerformanceMetrics(
        period_name=period_name,
        start_date=start_date,
        end_date=end_date,
        total_days=total_days,
        initial_cash=start_equity,
        final_equity=final_equity,
        cumulative_return=cumulative_return,
        max_drawdown=max_dd,
        num_fills=len(window_fills),
        total_fees_paid=total_fees,
        position_at_start=position_at_start,
    )

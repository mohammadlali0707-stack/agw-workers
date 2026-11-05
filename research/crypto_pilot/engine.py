"""Deterministic historical backtesting engine with exact accounting and no-lookahead execution."""

from datetime import date
from typing import List, Optional

from .metrics import calculate_metrics
from .models import (
    BacktestResult,
    Candle,
    DailyRecord,
    Fill,
    OrderSide,
    PerformanceMetrics,
    Position,
    validate_candles_sequence,
)
from .strategy import SMA20_50Strategy


class BacktestEngine:
    """
    Deterministic spot long/cash backtester.
    
    Key Properties:
    - Long / Cash only (no leverage, no short selling).
    - Decisions at completed day t close.
    - Execution at day t+1 Open (strictly no lookahead).
    - Configurable fee rate and slippage rate per fill.
    - Deterministic Buy & Hold benchmark with identical costs.
    - Chronological held-out window evaluation.
    """

    def __init__(
        self,
        initial_cash: float = 10000.0,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        fast_window: int = 20,
        slow_window: int = 50,
        held_out_ratio: float = 0.3,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError(f"initial_cash must be > 0, got {initial_cash}")
        if fee_rate < 0:
            raise ValueError(f"fee_rate cannot be negative, got {fee_rate}")
        if slippage_rate < 0:
            raise ValueError(f"slippage_rate cannot be negative, got {slippage_rate}")
        if not (0.0 <= held_out_ratio < 1.0):
            raise ValueError(f"held_out_ratio must be in [0.0, 1.0), got {held_out_ratio}")

        self.initial_cash = initial_cash
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.held_out_ratio = held_out_ratio
        self.strategy = SMA20_50Strategy(fast_window=fast_window, slow_window=slow_window)

    def run(self, candles: List[Candle]) -> BacktestResult:
        """
        Execute deterministic backtest on chronological list of candles.
        Enforces strict structural and chronological validation on input candles.
        """
        # Strict validation on the sequence of candles
        validate_candles_sequence(candles)

        # 1. Run Strategy Backtest
        strategy_records, strategy_fills = self._simulate_strategy(candles)

        # 2. Run Buy & Hold Comparator
        bh_records, bh_fills = self._simulate_buy_and_hold(candles)

        # 3. Compute Metrics for Full Period
        strat_full_metrics = calculate_metrics(
            period_name="Strategy Full Period",
            daily_records=strategy_records,
            fills=strategy_fills,
            initial_cash=self.initial_cash,
            position_at_start=Position.CASH,
        )

        bh_full_metrics = calculate_metrics(
            period_name="Buy & Hold Benchmark",
            daily_records=bh_records,
            fills=bh_fills,
            initial_cash=self.initial_cash,
            position_at_start=Position.CASH,
        )

        # 4. Chronological Held-Out Window Split (if applicable)
        in_sample_metrics: Optional[PerformanceMetrics] = None
        held_out_metrics: Optional[PerformanceMetrics] = None
        in_sample_bh_metrics: Optional[PerformanceMetrics] = None
        held_out_bh_metrics: Optional[PerformanceMetrics] = None

        n = len(strategy_records)
        if self.held_out_ratio > 0.0 and n >= 2:
            split_idx = int(n * (1.0 - self.held_out_ratio))
            # Ensure split leaves at least 1 record in each partition
            split_idx = max(1, min(n - 1, split_idx))

            is_records = strategy_records[:split_idx]
            ho_records = strategy_records[split_idx:]
            is_end_date = is_records[-1].date
            ho_start_date = ho_records[0].date

            is_bh_records = bh_records[:split_idx]
            ho_bh_records = bh_records[split_idx:]

            # Explicitly slice fills to prevent cross-window contamination
            is_fills = [f for f in strategy_fills if f.date <= is_end_date]
            ho_fills = [f for f in strategy_fills if f.date >= ho_start_date]

            is_bh_fills = [f for f in bh_fills if f.date <= is_end_date]
            ho_bh_fills = [f for f in bh_fills if f.date >= ho_start_date]

            # In-Sample Metrics
            in_sample_metrics = calculate_metrics(
                period_name="Strategy In-Sample Window",
                daily_records=is_records,
                fills=is_fills,
                initial_cash=self.initial_cash,
                position_at_start=Position.CASH,
            )

            in_sample_bh_metrics = calculate_metrics(
                period_name="Buy & Hold In-Sample Window",
                daily_records=is_bh_records,
                fills=is_bh_fills,
                initial_cash=self.initial_cash,
                position_at_start=Position.CASH,
            )

            # Held-Out Metrics
            # Note boundary position carried over from in-sample period
            pos_at_ho_start = is_records[-1].position
            ho_strat_start_equity = is_records[-1].equity
            ho_bh_start_equity = is_bh_records[-1].equity

            held_out_metrics = calculate_metrics(
                period_name="Strategy Held-Out Window",
                daily_records=ho_records,
                fills=ho_fills,
                initial_cash=ho_strat_start_equity,
                position_at_start=pos_at_ho_start,
            )

            held_out_bh_metrics = calculate_metrics(
                period_name="Buy & Hold Held-Out Window",
                daily_records=ho_bh_records,
                fills=ho_bh_fills,
                initial_cash=ho_bh_start_equity,
                position_at_start=Position.LONG,
            )

        return BacktestResult(
            strategy_metrics=strat_full_metrics,
            buy_and_hold_metrics=bh_full_metrics,
            in_sample_metrics=in_sample_metrics,
            held_out_metrics=held_out_metrics,
            in_sample_buy_and_hold_metrics=in_sample_bh_metrics,
            held_out_buy_and_hold_metrics=held_out_bh_metrics,
            fills=strategy_fills,
            daily_records=strategy_records,
            buy_and_hold_fills=bh_fills,
        )

    def _simulate_strategy(self, candles: List[Candle]):
        cash = self.initial_cash
        units = 0.0
        current_position = Position.CASH
        pending_signal: Optional[Position] = None

        daily_records: List[DailyRecord] = []
        fills: List[Fill] = []
        close_history: List[float] = []

        peak_equity = self.initial_cash

        for t, candle in enumerate(candles):
            # 1. Morning of day t: Fill any pending order decided at close of day t-1
            if pending_signal is not None and pending_signal != current_position:
                if pending_signal == Position.LONG and cash > 0:
                    # BUY fill at day t Open
                    base_price = candle.open
                    eff_price = base_price * (1.0 + self.slippage_rate)
                    # Cost = units * eff_price * (1 + fee_rate) == cash
                    units_to_buy = cash / (eff_price * (1.0 + self.fee_rate))
                    gross_value = units_to_buy * eff_price
                    fee = gross_value * self.fee_rate
                    total_outflow = gross_value + fee

                    cash = max(0.0, cash - total_outflow)
                    units += units_to_buy
                    current_position = Position.LONG

                    fills.append(
                        Fill(
                            date=candle.time,
                            side=OrderSide.BUY,
                            base_price=base_price,
                            effective_price=eff_price,
                            units=units_to_buy,
                            gross_value=gross_value,
                            fee=fee,
                            net_cash_flow=-total_outflow,
                            resulting_cash=cash,
                            resulting_units=units,
                            reason="SMA20 > SMA50 golden cross signal",
                        )
                    )

                elif pending_signal == Position.CASH and units > 0:
                    # SELL fill at day t Open
                    base_price = candle.open
                    eff_price = base_price * (1.0 - self.slippage_rate)
                    gross_value = units * eff_price
                    fee = gross_value * self.fee_rate
                    net_inflow = gross_value - fee

                    cash += net_inflow
                    units_sold = units
                    units = 0.0
                    current_position = Position.CASH

                    fills.append(
                        Fill(
                            date=candle.time,
                            side=OrderSide.SELL,
                            base_price=base_price,
                            effective_price=eff_price,
                            units=units_sold,
                            gross_value=gross_value,
                            fee=fee,
                            net_cash_flow=net_inflow,
                            resulting_cash=cash,
                            resulting_units=units,
                            reason="SMA20 <= SMA50 death cross / cash signal",
                        )
                    )

            # 2. Evening of day t: Bar completes at close
            close_history.append(candle.close)
            eod_equity = cash + (units * candle.close)
            if eod_equity > peak_equity:
                peak_equity = eod_equity
            drawdown = (peak_equity - eod_equity) / peak_equity if peak_equity > 0 else 0.0

            # 3. Strategy decision strictly at day t close (NO lookahead)
            target_signal, sma_fast, sma_slow = self.strategy.decide_target_position(close_history)
            pending_signal = target_signal

            daily_records.append(
                DailyRecord(
                    date=candle.time,
                    close=candle.close,
                    sma_fast=sma_fast,
                    sma_slow=sma_slow,
                    target_signal=target_signal,
                    position=current_position,
                    cash=cash,
                    units=units,
                    equity=eod_equity,
                    peak_equity=peak_equity,
                    drawdown=drawdown,
                )
            )

        return daily_records, fills

    def _simulate_buy_and_hold(self, candles: List[Candle]):
        """
        Deterministic buy & hold benchmark.
        Buys on the first available open (Candle 0 Open) with identical fee and slippage.
        """
        first_candle = candles[0]
        base_price = first_candle.open
        eff_price = base_price * (1.0 + self.slippage_rate)

        units = self.initial_cash / (eff_price * (1.0 + self.fee_rate))
        gross_value = units * eff_price
        fee = gross_value * self.fee_rate
        total_cost = gross_value + fee
        cash = max(0.0, self.initial_cash - total_cost)

        bh_fills = [
            Fill(
                date=first_candle.time,
                side=OrderSide.BUY,
                base_price=base_price,
                effective_price=eff_price,
                units=units,
                gross_value=gross_value,
                fee=fee,
                net_cash_flow=-total_cost,
                resulting_cash=cash,
                resulting_units=units,
                reason="Buy & Hold initial allocation",
            )
        ]

        bh_records: List[DailyRecord] = []
        peak_equity = self.initial_cash

        for candle in candles:
            equity = cash + (units * candle.close)
            if equity > peak_equity:
                peak_equity = equity
            drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0

            bh_records.append(
                DailyRecord(
                    date=candle.time,
                    close=candle.close,
                    sma_fast=None,
                    sma_slow=None,
                    target_signal=Position.LONG,
                    position=Position.LONG,
                    cash=cash,
                    units=units,
                    equity=equity,
                    peak_equity=peak_equity,
                    drawdown=drawdown,
                )
            )

        return bh_records, bh_fills

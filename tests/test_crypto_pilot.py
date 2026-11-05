"""
Automated test suite for crypto research pilot backtester.

Validates:
1. Strict data validation (chronological order, duplicate dates, missing dates, nonpositive prices, impossible candles).
2. Direct validation in BacktestEngine.run(candles) to guard against raw input corruption.
3. Lookahead prevention (signal at day t close, fill at day t+1 open, independence from t+1 intraday/close).
4. Trading costs (exact fee and slippage math, monotonic reduction in equity).
5. Buy & Hold comparator (identical initial capital, exact net cash flow accounting, 1 fill).
6. Initial capital accounting & no-leverage guarantee.
7. Chronological held-out window evaluation without parameter tuning & fill slicing.
"""

import copy
import os
import unittest
from datetime import date, timedelta
from io import StringIO

from research.crypto_pilot.data_loader import load_candles_from_csv
from research.crypto_pilot.engine import BacktestEngine
from research.crypto_pilot.models import (
    Candle,
    OrderSide,
    Position,
    ValidationError,
)
from research.crypto_pilot.strategy import SMA20_50Strategy


class TestDataValidation(unittest.TestCase):
    """Test suite for strict data validation on daily OHLC CSVs."""

    def test_valid_csv_loading(self):
        csv_data = """time,open,high,low,close,volume
2023-01-01,100,105,95,102,1000
2023-01-02,102,108,100,107,1100
2023-01-03,107,110,104,109,1050
"""
        candles = load_candles_from_csv(StringIO(csv_data))
        self.assertEqual(len(candles), 3)
        self.assertEqual(candles[0].time, date(2023, 1, 1))
        self.assertEqual(candles[1].open, 102.0)
        self.assertEqual(candles[2].close, 109.0)

    def test_reject_duplicate_dates(self):
        csv_data = """time,open,high,low,close,volume
2023-01-01,100,105,95,102,1000
2023-01-02,102,108,100,107,1100
2023-01-02,107,110,104,109,1050
"""
        with self.assertRaises(ValidationError) as ctx:
            load_candles_from_csv(StringIO(csv_data))
        self.assertIn("duplicate daily date", str(ctx.exception))

    def test_reject_missing_dates(self):
        csv_data = """time,open,high,low,close,volume
2023-01-01,100,105,95,102,1000
2023-01-02,102,108,100,107,1100
2023-01-05,107,110,104,109,1050
"""
        with self.assertRaises(ValidationError) as ctx:
            load_candles_from_csv(StringIO(csv_data))
        self.assertIn("missing daily date gap", str(ctx.exception))

    def test_reject_out_of_order_dates(self):
        csv_data = """time,open,high,low,close,volume
2023-01-02,102,108,100,107,1100
2023-01-01,100,105,95,102,1000
"""
        with self.assertRaises(ValidationError) as ctx:
            load_candles_from_csv(StringIO(csv_data))
        self.assertIn("out-of-order date", str(ctx.exception))

    def test_reject_nonpositive_prices(self):
        cases = [
            ("open <= 0", "2023-01-01,0,105,95,102,1000", "open price must be > 0"),
            ("high <= 0", "2023-01-01,100,-1,95,102,1000", "high price must be > 0"),
            ("low <= 0", "2023-01-01,100,105,0,102,1000", "low price must be > 0"),
            ("close <= 0", "2023-01-01,100,105,95,-5,1000", "close price must be > 0"),
        ]
        for name, row, err_substring in cases:
            with self.subTest(case=name):
                csv_data = f"time,open,high,low,close,volume\n{row}\n"
                with self.assertRaises(ValidationError) as ctx:
                    load_candles_from_csv(StringIO(csv_data))
                self.assertIn(err_substring, str(ctx.exception))

    def test_reject_negative_volume(self):
        csv_data = """time,open,high,low,close,volume
2023-01-01,100,105,95,102,-10
"""
        with self.assertRaises(ValidationError) as ctx:
            load_candles_from_csv(StringIO(csv_data))
        self.assertIn("volume cannot be negative", str(ctx.exception))

    def test_reject_impossible_candles(self):
        cases = [
            ("high < low", "2023-01-01,100,90,105,100,1000", "high (90.0) < low (105.0)"),
            ("open > high", "2023-01-01,110,105,95,102,1000", "open (110.0) outside"),
            ("open < low", "2023-01-01,90,105,95,102,1000", "open (90.0) outside"),
            ("close > high", "2023-01-01,100,105,95,110,1000", "close (110.0) outside"),
            ("close < low", "2023-01-01,100,105,95,90,1000", "close (90.0) outside"),
        ]
        for name, row, err_substring in cases:
            with self.subTest(case=name):
                csv_data = f"time,open,high,low,close,volume\n{row}\n"
                with self.assertRaises(ValidationError) as ctx:
                    load_candles_from_csv(StringIO(csv_data))
                self.assertIn(err_substring, str(ctx.exception))

    def test_reject_missing_required_headers(self):
        csv_data = """date,open,high,low,close
2023-01-01,100,105,95,102
"""
        with self.assertRaises(ValidationError) as ctx:
            load_candles_from_csv(StringIO(csv_data))
        self.assertIn("missing required columns", str(ctx.exception))

    def test_load_all_invalid_fixtures_on_disk(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "..", "research", "crypto_pilot", "fixtures")
        invalid_files = [
            "invalid_duplicate_date.csv",
            "invalid_missing_date.csv",
            "invalid_nonpositive_price.csv",
            "invalid_impossible_candle.csv",
            "invalid_negative_volume.csv",
            "invalid_out_of_order.csv",
        ]
        for filename in invalid_files:
            filepath = os.path.join(fixtures_dir, filename)
            if os.path.exists(filepath):
                with self.subTest(file=filename):
                    with self.assertRaises(ValidationError):
                        load_candles_from_csv(filepath)

    def test_engine_run_validates_raw_candles(self):
        """Engine.run() must strictly re-validate raw candle inputs."""
        engine = BacktestEngine()

        # 1. Empty list
        with self.assertRaises(ValidationError):
            engine.run([])

        # 2. Missing gap candle list
        gap_candles = [
            Candle(date(2023, 1, 1), 100, 105, 95, 100, 1000),
            Candle(date(2023, 1, 3), 100, 105, 95, 100, 1000),  # Gap of 2 days
        ]
        with self.assertRaises(ValidationError) as ctx:
            engine.run(gap_candles)
        self.assertIn("missing daily date gap", str(ctx.exception))

        # 3. Duplicate date candle list
        dup_candles = [
            Candle(date(2023, 1, 1), 100, 105, 95, 100, 1000),
            Candle(date(2023, 1, 1), 100, 105, 95, 100, 1000),
        ]
        with self.assertRaises(ValidationError) as ctx:
            engine.run(dup_candles)
        self.assertIn("duplicate daily date", str(ctx.exception))


class TestLookaheadPrevention(unittest.TestCase):
    """
    Test suite to prove no lookahead bias exists.
    - Day t signal decided at Day t close.
    - Fill happens at Day t+1 open.
    - Day t+1 intraday prices and close have zero impact on Day t signal and fill price.
    """

    def setUp(self):
        # Generate 60 days of synthetic data
        start_d = date(2023, 1, 1)
        self.candles = []
        for i in range(60):
            # Flat at 100 for 40 days, then jump to 200 to trigger SMA20 > SMA50
            p = 100.0 if i < 45 else 200.0
            self.candles.append(
                Candle(
                    time=start_d + timedelta(days=i),
                    open=p,
                    high=p + 5.0,
                    low=p - 5.0,
                    close=p,
                    volume=1000.0,
                )
            )

    def test_decision_and_fill_timing(self):
        engine = BacktestEngine(fast_window=20, slow_window=50, held_out_ratio=0.0)
        result = engine.run(self.candles)

        self.assertGreaterEqual(len(result.fills), 1)
        first_fill = result.fills[0]

        first_signal_day_idx = None
        for idx, rec in enumerate(result.daily_records):
            if rec.target_signal == Position.LONG:
                first_signal_day_idx = idx
                break

        self.assertIsNotNone(first_signal_day_idx)
        fill_candle = self.candles[first_signal_day_idx + 1]

        # Verification 1: On the signal day at close, the position was STILL CASH!
        self.assertEqual(result.daily_records[first_signal_day_idx].position, Position.CASH)
        self.assertEqual(result.daily_records[first_signal_day_idx].units, 0.0)

        # Verification 2: Fill date is exactly Day t+1
        self.assertEqual(first_fill.date, fill_candle.time)
        self.assertEqual(first_fill.side, OrderSide.BUY)
        self.assertEqual(first_fill.base_price, fill_candle.open)

        # Verification 3: On Day t+1, position is now LONG
        self.assertEqual(result.daily_records[first_signal_day_idx + 1].position, Position.LONG)

    def test_altering_future_data_does_not_affect_past_signals(self):
        """
        Altering day t+1 High, Low, Close, Volume must have ZERO effect on:
        1. Day t decision
        2. Day t+1 fill price (since fill price depends strictly on day t+1 Open)
        """
        engine = BacktestEngine(fast_window=20, slow_window=50, held_out_ratio=0.0)
        res_original = engine.run(self.candles)

        first_signal_day_idx = next(
            i for i, r in enumerate(res_original.daily_records) if r.target_signal == Position.LONG
        )
        fill_day_idx = first_signal_day_idx + 1

        modified_candles = copy.deepcopy(self.candles)
        orig_fill_candle = modified_candles[fill_day_idx]
        modified_candles[fill_day_idx] = Candle(
            time=orig_fill_candle.time,
            open=orig_fill_candle.open,  # Open remains identical
            high=orig_fill_candle.open + 500.0,  # Massive intraday spike
            low=orig_fill_candle.open - 50.0,
            close=orig_fill_candle.open + 300.0,
            volume=999999.0,
        )

        res_modified = engine.run(modified_candles)

        # Signal at Day t must be identical
        self.assertEqual(
            res_original.daily_records[first_signal_day_idx].target_signal,
            res_modified.daily_records[first_signal_day_idx].target_signal,
        )
        # Fill execution price on Day t+1 must be identical
        self.assertEqual(res_original.fills[0].effective_price, res_modified.fills[0].effective_price)
        self.assertEqual(res_original.fills[0].units, res_modified.fills[0].units)
        self.assertEqual(res_original.fills[0].fee, res_modified.fills[0].fee)


class TestTradingCosts(unittest.TestCase):
    """Test suite verifying exact fee and slippage math."""

    def test_exact_buy_accounting(self):
        engine = BacktestEngine(
            initial_cash=10000.0,
            fee_rate=0.001,  # 0.1%
            slippage_rate=0.0005,  # 0.05%
            fast_window=2,
            slow_window=4,
            held_out_ratio=0.0,
        )

        start_d = date(2023, 1, 1)
        candles = [
            Candle(start_d + timedelta(days=0), 100, 105, 95, 100, 1000),
            Candle(start_d + timedelta(days=1), 100, 105, 95, 100, 1000),
            Candle(start_d + timedelta(days=2), 100, 105, 95, 100, 1000),
            Candle(start_d + timedelta(days=3), 100, 205, 95, 200, 1000),
            Candle(start_d + timedelta(days=4), 150, 155, 145, 150, 1000),
            Candle(start_d + timedelta(days=5), 150, 155, 145, 150, 1000),
        ]

        result = engine.run(candles)
        self.assertEqual(len(result.fills), 1)
        fill = result.fills[0]

        expected_eff_price = 150.0 * (1.0 + 0.0005)  # 150.075
        expected_units = 10000.0 / (expected_eff_price * (1.0 + 0.001))
        expected_gross = expected_units * expected_eff_price
        expected_fee = expected_gross * 0.001
        total_outflow = expected_gross + expected_fee

        self.assertAlmostEqual(fill.base_price, 150.0, places=4)
        self.assertAlmostEqual(fill.effective_price, expected_eff_price, places=6)
        self.assertAlmostEqual(fill.units, expected_units, places=6)
        self.assertAlmostEqual(fill.gross_value, expected_gross, places=6)
        self.assertAlmostEqual(fill.fee, expected_fee, places=6)
        self.assertAlmostEqual(fill.net_cash_flow, -total_outflow, places=4)
        self.assertAlmostEqual(fill.resulting_cash, 0.0, places=4)

    def test_costs_monotonically_reduce_terminal_equity(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "..", "research", "crypto_pilot", "fixtures")
        btc_file = os.path.join(fixtures_dir, "valid_daily_btc.csv")
        candles = load_candles_from_csv(btc_file)

        e_zero_cost = BacktestEngine(initial_cash=10000.0, fee_rate=0.0, slippage_rate=0.0)
        e_low_cost = BacktestEngine(initial_cash=10000.0, fee_rate=0.001, slippage_rate=0.0005)
        e_high_cost = BacktestEngine(initial_cash=10000.0, fee_rate=0.005, slippage_rate=0.0020)

        res_zero = e_zero_cost.run(candles)
        res_low = e_low_cost.run(candles)
        res_high = e_high_cost.run(candles)

        self.assertGreater(res_zero.strategy_metrics.final_equity, res_low.strategy_metrics.final_equity)
        self.assertGreater(res_low.strategy_metrics.final_equity, res_high.strategy_metrics.final_equity)


class TestBuyHoldComparator(unittest.TestCase):
    """Test suite verifying Buy & Hold benchmark behavior."""

    def test_buy_and_hold_properties_and_cash_consistency(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "..", "research", "crypto_pilot", "fixtures")
        btc_file = os.path.join(fixtures_dir, "valid_daily_btc.csv")
        candles = load_candles_from_csv(btc_file)

        engine = BacktestEngine(initial_cash=10000.0, fee_rate=0.001, slippage_rate=0.0005)
        res = engine.run(candles)

        bh = res.buy_and_hold_metrics
        self.assertEqual(bh.num_fills, 1)
        self.assertEqual(len(res.buy_and_hold_fills), 1)
        self.assertEqual(bh.initial_cash, 10000.0)

        first_fill = res.buy_and_hold_fills[0]
        first_candle = candles[0]
        expected_eff_px = first_candle.open * (1.0 + 0.0005)
        expected_units = 10000.0 / (expected_eff_px * (1.0 + 0.001))
        expected_gross = expected_units * expected_eff_px
        expected_fee = expected_gross * 0.001
        total_cost = expected_gross + expected_fee

        self.assertEqual(first_fill.date, first_candle.time)
        self.assertAlmostEqual(first_fill.effective_price, expected_eff_px, places=6)
        self.assertAlmostEqual(first_fill.units, expected_units, places=6)
        # Net cash flow matches exactly -(gross + fee)
        self.assertAlmostEqual(first_fill.net_cash_flow, -total_cost, places=4)
        self.assertAlmostEqual(first_fill.resulting_cash, 10000.0 - total_cost, places=4)
        self.assertGreaterEqual(first_fill.resulting_cash, 0.0)


class TestInitialCapitalAccounting(unittest.TestCase):
    """Test suite ensuring exact conservation of capital and no leverage."""

    def test_no_leverage_and_conservation(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "..", "research", "crypto_pilot", "fixtures")
        btc_file = os.path.join(fixtures_dir, "valid_daily_btc.csv")
        candles = load_candles_from_csv(btc_file)

        engine = BacktestEngine(initial_cash=10000.0, fee_rate=0.001, slippage_rate=0.0005)
        res = engine.run(candles)

        for record in res.daily_records:
            self.assertGreaterEqual(record.cash, 0.0)
            self.assertGreaterEqual(record.units, 0.0)

            expected_equity = record.cash + (record.units * record.close)
            self.assertAlmostEqual(record.equity, expected_equity, places=4)

            self.assertGreaterEqual(record.drawdown, 0.0)
            self.assertLessEqual(record.drawdown, 1.0)


class TestHeldOutWindow(unittest.TestCase):
    """Test suite verifying chronological held-out window evaluation without parameter tuning."""

    def test_held_out_window_partition_and_fills_slicing(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "..", "research", "crypto_pilot", "fixtures")
        btc_file = os.path.join(fixtures_dir, "valid_daily_btc.csv")
        candles = load_candles_from_csv(btc_file)

        engine = BacktestEngine(initial_cash=10000.0, held_out_ratio=0.3)
        res = engine.run(candles)

        self.assertIsNotNone(res.in_sample_metrics)
        self.assertIsNotNone(res.held_out_metrics)
        self.assertIsNotNone(res.in_sample_buy_and_hold_metrics)
        self.assertIsNotNone(res.held_out_buy_and_hold_metrics)

        is_m = res.in_sample_metrics
        ho_m = res.held_out_metrics

        # Partitions sum to total days
        self.assertEqual(is_m.total_days + ho_m.total_days, res.strategy_metrics.total_days)

        # Held-out window starts the day after in-sample ends
        self.assertEqual(ho_m.start_date, is_m.end_date + timedelta(days=1))

        # Held-out window starting equity matches in-sample ending equity
        self.assertAlmostEqual(ho_m.initial_cash, is_m.final_equity, places=2)

        # Sliced fills: in-sample fills + held-out fills sum exactly to total fills (no leak)
        self.assertEqual(is_m.num_fills + ho_m.num_fills, res.strategy_metrics.num_fills)


if __name__ == "__main__":
    unittest.main()

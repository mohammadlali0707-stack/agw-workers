"""CLI entry point for running the crypto pilot backtester."""

import argparse
import json
import sys
from typing import Any, Dict

from .data_loader import load_candles_from_csv
from .engine import BacktestEngine
from .models import BacktestResult, PerformanceMetrics, ValidationError


def _metrics_to_dict(m: PerformanceMetrics) -> Dict[str, Any]:
    return {
        "period_name": m.period_name,
        "start_date": m.start_date.isoformat(),
        "end_date": m.end_date.isoformat(),
        "total_days": m.total_days,
        "initial_cash": round(m.initial_cash, 2),
        "final_equity": round(m.final_equity, 2),
        "cumulative_return_pct": round(m.cumulative_return * 100.0, 4),
        "max_drawdown_pct": round(m.max_drawdown * 100.0, 4),
        "num_fills": m.num_fills,
        "total_fees_paid": round(m.total_fees_paid, 2),
        "position_at_start": m.position_at_start.value,
    }


def format_table(results: BacktestResult) -> str:
    lines = []
    lines.append("=" * 116)
    lines.append("CRYPTO RESEARCH-ONLY PILOT BACKTEST REPORT (SMA 20/50)")
    lines.append("=" * 116)
    lines.append(
        f"{'Metric / Period':<24} | {'Strat (Full)':<13} | {'B&H (Full)':<13} | {'Strat (IS)':<13} | {'B&H (IS)':<13} | {'Strat (HO)':<13} | {'B&H (HO)':<13}"
    )
    lines.append("-" * 116)

    s = results.strategy_metrics
    b = results.buy_and_hold_metrics
    is_s = results.in_sample_metrics
    is_b = results.in_sample_buy_and_hold_metrics
    ho_s = results.held_out_metrics
    ho_b = results.held_out_buy_and_hold_metrics

    def row(label, f1, f2, f3, f4, f5, f6):
        return f"{label:<24} | {f1:<13} | {f2:<13} | {f3:<13} | {f4:<13} | {f5:<13} | {f6:<13}"

    lines.append(
        row(
            "Start Date",
            s.start_date.isoformat(),
            b.start_date.isoformat(),
            is_s.start_date.isoformat() if is_s else "N/A",
            is_b.start_date.isoformat() if is_b else "N/A",
            ho_s.start_date.isoformat() if ho_s else "N/A",
            ho_b.start_date.isoformat() if ho_b else "N/A",
        )
    )
    lines.append(
        row(
            "End Date",
            s.end_date.isoformat(),
            b.end_date.isoformat(),
            is_s.end_date.isoformat() if is_s else "N/A",
            is_b.end_date.isoformat() if is_b else "N/A",
            ho_s.end_date.isoformat() if ho_s else "N/A",
            ho_b.end_date.isoformat() if ho_b else "N/A",
        )
    )
    lines.append(
        row(
            "Total Days",
            str(s.total_days),
            str(b.total_days),
            str(is_s.total_days) if is_s else "N/A",
            str(is_b.total_days) if is_b else "N/A",
            str(ho_s.total_days) if ho_s else "N/A",
            str(ho_b.total_days) if ho_b else "N/A",
        )
    )
    lines.append(
        row(
            "Initial Equity ($)",
            f"{s.initial_cash:,.2f}",
            f"{b.initial_cash:,.2f}",
            f"{is_s.initial_cash:,.2f}" if is_s else "N/A",
            f"{is_b.initial_cash:,.2f}" if is_b else "N/A",
            f"{ho_s.initial_cash:,.2f}" if ho_s else "N/A",
            f"{ho_b.initial_cash:,.2f}" if ho_b else "N/A",
        )
    )
    lines.append(
        row(
            "Final Equity ($)",
            f"{s.final_equity:,.2f}",
            f"{b.final_equity:,.2f}",
            f"{is_s.final_equity:,.2f}" if is_s else "N/A",
            f"{is_b.final_equity:,.2f}" if is_b else "N/A",
            f"{ho_s.final_equity:,.2f}" if ho_s else "N/A",
            f"{ho_b.final_equity:,.2f}" if ho_b else "N/A",
        )
    )
    lines.append(
        row(
            "Cumulative Return",
            f"{s.cumulative_return * 100.0:+.2f}%",
            f"{b.cumulative_return * 100.0:+.2f}%",
            f"{is_s.cumulative_return * 100.0:+.2f}%" if is_s else "N/A",
            f"{is_b.cumulative_return * 100.0:+.2f}%" if is_b else "N/A",
            f"{ho_s.cumulative_return * 100.0:+.2f}%" if ho_s else "N/A",
            f"{ho_b.cumulative_return * 100.0:+.2f}%" if ho_b else "N/A",
        )
    )
    lines.append(
        row(
            "Max Drawdown",
            f"{s.max_drawdown * 100.0:.2f}%",
            f"{b.max_drawdown * 100.0:.2f}%",
            f"{is_s.max_drawdown * 100.0:.2f}%" if is_s else "N/A",
            f"{is_b.max_drawdown * 100.0:.2f}%" if is_b else "N/A",
            f"{ho_s.max_drawdown * 100.0:.2f}%" if ho_s else "N/A",
            f"{ho_b.max_drawdown * 100.0:.2f}%" if ho_b else "N/A",
        )
    )
    lines.append(
        row(
            "Number of Fills",
            str(s.num_fills),
            str(b.num_fills),
            str(is_s.num_fills) if is_s else "N/A",
            str(is_b.num_fills) if is_b else "N/A",
            str(ho_s.num_fills) if ho_s else "N/A",
            str(ho_b.num_fills) if ho_b else "N/A",
        )
    )
    lines.append(
        row(
            "Total Fees Paid ($)",
            f"{s.total_fees_paid:,.2f}",
            f"{b.total_fees_paid:,.2f}",
            f"{is_s.total_fees_paid:,.2f}" if is_s else "N/A",
            f"{is_b.total_fees_paid:,.2f}" if is_b else "N/A",
            f"{ho_s.total_fees_paid:,.2f}" if ho_s else "N/A",
            f"{ho_b.total_fees_paid:,.2f}" if ho_b else "N/A",
        )
    )
    lines.append("-" * 116)

    if results.fills:
        lines.append("\nExecuted Strategy Fills Log:")
        lines.append(
            f"{'Date':<12} | {'Side':<4} | {'Base Px':<10} | {'Eff Px':<10} | {'Units':<12} | {'Fee ($)':<8} | {'Net Cash Flow ($)':<18}"
        )
        lines.append("-" * 88)
        for f in results.fills:
            lines.append(
                f"{f.date.isoformat():<12} | {f.side.value:<4} | {f.base_price:<10.2f} | {f.effective_price:<10.2f} | {f.units:<12.6f} | {f.fee:<8.2f} | {f.net_cash_flow:<+18.2f}"
            )
    else:
        lines.append("\nNo strategy fills executed during this period (remained 100% Cash).")

    lines.append("\nAssumptions & Limitations:")
    lines.append("- RESEARCH ONLY: Strictly educational and research. No claim of profitable edge or live trading readiness.")
    lines.append("- No Lookahead: Signals computed at Day t Close; fills executed at Day t+1 Open.")
    lines.append("- Slippage & Fees: Applied symmetrically to all buy and sell fills based on user configuration.")
    lines.append("- Liquidity Assumption: Assumes infinite liquidity at Day t+1 Open price (no market impact modeling).")
    lines.append("- Lagging Indicator: Fixed SMA 20/50 is prone to whipsaws and lag during range-bound regimes.")
    lines.append("=" * 116)
    return "\n".join(lines)


def main(args=None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic historical backtester for crypto spot long/cash SMA 20/50 pilot."
    )
    parser.add_argument("--data", required=True, help="Path to daily OHLC CSV file (time,open,high,low,close,volume)")
    parser.add_argument("--initial-cash", type=float, default=10000.0, help="Initial portfolio cash (default: 10000.0)")
    parser.add_argument("--fee", type=float, default=0.001, help="Transaction fee rate per fill (default: 0.001 = 0.1%%)")
    parser.add_argument("--slippage", type=float, default=0.0005, help="Slippage rate per fill (default: 0.0005 = 0.05%%)")
    parser.add_argument("--fast-window", type=int, default=20, help="Fast SMA window in daily bars (default: 20)")
    parser.add_argument("--slow-window", type=int, default=50, help="Slow SMA window in daily bars (default: 50)")
    parser.add_argument("--held-out-ratio", type=float, default=0.3, help="Chronological held-out fraction (default: 0.3)")
    parser.add_argument("--json", action="store_true", help="Output summary results as JSON")

    parsed = parser.parse_args(args)

    try:
        candles = load_candles_from_csv(parsed.data)
        engine = BacktestEngine(
            initial_cash=parsed.initial_cash,
            fee_rate=parsed.fee,
            slippage_rate=parsed.slippage,
            fast_window=parsed.fast_window,
            slow_window=parsed.slow_window,
            held_out_ratio=parsed.held_out_ratio,
        )
        result = engine.run(candles)

        if parsed.json:
            payload = {
                "strategy": _metrics_to_dict(result.strategy_metrics),
                "buy_and_hold": _metrics_to_dict(result.buy_and_hold_metrics),
                "in_sample": _metrics_to_dict(result.in_sample_metrics) if result.in_sample_metrics else None,
                "in_sample_buy_and_hold": _metrics_to_dict(result.in_sample_buy_and_hold_metrics) if result.in_sample_buy_and_hold_metrics else None,
                "held_out": _metrics_to_dict(result.held_out_metrics) if result.held_out_metrics else None,
                "held_out_buy_and_hold": _metrics_to_dict(result.held_out_buy_and_hold_metrics) if result.held_out_buy_and_hold_metrics else None,
                "fills_count": len(result.fills),
            }
            print(json.dumps(payload, indent=2))
        else:
            print(format_table(result))

        return 0

    except ValidationError as e:
        print(f"Data Validation Error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Error executing backtest: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

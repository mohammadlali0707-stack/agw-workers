# Crypto Research-Only Pilot (SMA 20/50 Spot Backtester)

> **RESEARCH ONLY DISCLAIMER**: This software is designed strictly for research, retrospective analysis, and educational methodology validation. It does **NOT** make any claim of profitable edge, financial returns, or algorithmic trading viability. It does **NOT** connect to live exchange APIs, issue orders, manage funds, or perform automated trading.

---

## 1. Overview & Objectives

`research/crypto_pilot` is a deterministic, auditable, dependency-free historical backtesting engine designed for cryptocurrency daily spot markets.

The pilot evaluates a classic trend-following rule:
- **Rule**: Fixed SMA 20 (fast) / SMA 50 (slow) crossover.
- **Position Mode**: Spot Long or Cash only (100% long or 100% cash).
- **Benchmark**: Deterministic Buy & Hold comparator starting with identical initial capital and subject to identical transaction cost modeling.
- **Out-of-Sample Evaluation**: Chronological held-out window evaluation without parameter tuning.

---

## 2. Key Architectural Guarantees

### A. Strict Data Validation
Crypto spot markets operate continuously 24/7/365 without weekend or holiday closures. The data loader enforces:
1. **Required Columns**: Case-insensitive CSV headers with `time`, `open`, `high`, `low`, `close`, `volume`.
2. **Chronological Monotonicity**: Dates must be strictly increasing. Out-of-order dates are rejected with `ValidationError`.
3. **No Duplicate Dates**: Identical dates in consecutive rows are rejected.
4. **No Missing Daily Dates**: Gaps exceeding 1 calendar day between consecutive candles are rejected.
5. **Positive Prices**: Nonpositive prices (`open <= 0`, `high <= 0`, `low <= 0`, `close <= 0`) are rejected.
6. **Valid Candles**: Impossible candle geometry (`high < low`, `open` or `close` outside `[low, high]`, `volume < 0`) is rejected.

### B. Absolute Lookahead Prevention
A common flaw in naive backtesting is using day $t$'s close or intraday prices to fill orders on day $t$.
This pilot strictly enforces the following timeline:
1. **Evening of Day $t$**: After day $t$'s candle closes, the portfolio is marked to market at $\text{Close}_t$, and indicators ($\text{SMA}_{20}(t)$, $\text{SMA}_{50}(t)$) are calculated using close prices strictly up to day $t$.
2. **Signal Generation**: If $\text{SMA}_{20}(t) > \text{SMA}_{50}(t)$, the target signal for the next session is `LONG`. Otherwise, it is `CASH`.
3. **Morning of Day $t+1$**: Any rebalancing order is filled at **$\text{Open}_{t+1}$**, adjusted for slippage.
4. **Intraday & Future Invariance**: Changes to Day $t+1$'s High, Low, Close, or Volume have zero effect on Day $t$'s decision or Day $t+1$'s fill price.

### C. Exact Execution Accounting & Trading Costs
- **No Leverage / No Shorting**: Spot long/cash only. Cash $\ge 0$ and asset units $\ge 0$ at all times.
- **Slippage Modeling**:
  - Buy fill price: $P_{\text{eff}} = \text{Open} \times (1 + \text{slippage\_rate})$
  - Sell fill price: $P_{\text{eff}} = \text{Open} \times (1 - \text{slippage\_rate})$
- **Fee Accounting**:
  - Fees are deducted from transaction capital: $\text{Fee} = \text{Gross Value} \times \text{fee\_rate}$.
  - Buy order unit allocation: $\text{Units} = \frac{\text{Cash}}{P_{\text{eff}} \times (1 + \text{fee\_rate})}$. Total cash spent exactly equals available cash.
  - Sell order net proceeds: $\text{Cash} = \text{Units} \times P_{\text{eff}} \times (1 - \text{fee\_rate})$.

### D. Buy & Hold Comparator
The benchmark starts with the identical `initial_cash` on Day 0 Open, incurs identical slippage and transaction fee upon entry, and tracks mark-to-market daily closing equity.

### E. Chronological Held-Out Window (No Parameter Tuning)
The timeline is cleanly partitioned into In-Sample (e.g. initial 70%) and Held-Out (e.g. final 30%) sub-periods without lookahead leakage. The fixed SMA 20/50 parameters are not tuned or optimized.

---

## 3. Assumptions and Limitations

1. **No Profitable Edge**: The SMA 20/50 rule is a standard textbook indicator. In sideways or range-bound markets, it frequently suffers from whipsaw losses, entering late at the top of a rally and exiting late at the bottom of a decline.
2. **Infinite Liquidity Assumption**: The model assumes that the full order quantity can be executed at the day's open price $\pm$ slippage without price impact or order book exhaustion.
3. **Daily Aggregation**: Intraday price paths and extreme intra-day volatility are not modeled beyond daily candle extremes.
4. **Zero Dependency**: Implemented purely using the Python standard library (`math`, `csv`, `datetime`, `dataclasses`, `argparse`, `unittest`) for full auditability, zero supply chain risk, and deterministic repeatability.

---

## 4. Usage Guide

### CLI Command
```bash
python3 -m research.crypto_pilot.cli --data research/crypto_pilot/fixtures/valid_daily_btc.csv
```

### Optional Arguments
- `--initial-cash FLOAT`: Starting cash in USD (default: `10000.0`).
- `--fee FLOAT`: Proportional fee rate per fill (default: `0.001` = 0.10%).
- `--slippage FLOAT`: Proportional slippage rate per fill (default: `0.0005` = 0.05%).
- `--fast-window INT`: Fast SMA period in days (default: `20`).
- `--slow-window INT`: Slow SMA period in days (default: `50`).
- `--held-out-ratio FLOAT`: Proportion reserved for held-out evaluation (default: `0.3`).
- `--json`: Output metrics as structured JSON.

---

## 5. Automated Tests

Run the dedicated test suite:
```bash
python3 -m unittest tests/test_crypto_pilot.py -v
```

The automated suite tests:
- `TestDataValidation`: Chronological order, duplicate dates, missing date gaps, negative/zero prices, negative volume, impossible candle geometry (`high < low`, open/close out of bounds).
- `TestLookaheadPrevention`: Signal at day $t$ close, execution at day $t+1$ open, intraday invariance of future bars.
- `TestTradingCosts`: Exact fee and slippage math, monotonic reduction in terminal equity with increased costs.
- `TestBuyHoldComparator`: Single fill, identical starting cash, identical entry costs.
- `TestInitialCapitalAccounting`: Non-negative cash/units, zero leverage, exact equity conservation.
- `TestHeldOutWindow`: Proper partitioning and reporting of held-out performance without parameter tuning.

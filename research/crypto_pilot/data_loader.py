"""Data loader and validator for daily OHLC CSV files in research pilot."""

import csv
from datetime import date, datetime
from io import StringIO
from typing import Iterable, List, Union

from .models import Candle, ValidationError

REQUIRED_COLUMNS = {"time", "open", "high", "low", "close", "volume"}


def parse_date_str(val: str, row_num: int) -> date:
    """Parse ISO8601 or YYYY-MM-DD date string strictly into datetime.date."""
    val = val.strip()
    if not val:
        raise ValidationError(f"Row {row_num}: empty time string")
    
    # Try ISO formats
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            pass
            
    # As a fallback, try fromisoformat if available
    try:
        return date.fromisoformat(val[:10])
    except Exception:
        raise ValidationError(f"Row {row_num}: invalid date format '{val}', expected YYYY-MM-DD or ISO-8601")


def load_candles_from_csv(source: Union[str, Iterable[str]]) -> List[Candle]:
    """
    Load and strictly validate daily OHLCV candles from a CSV file path or iterable of CSV lines.

    Validation rules enforced:
    - Required columns: time, open, high, low, close, volume.
    - Prices must be strictly positive (> 0).
    - Volume must be non-negative (>= 0).
    - Impossible candles rejected (high < low, open or close outside [low, high]).
    - Strict chronological validation (ascending order).
    - Duplicate dates rejected.
    - Missing daily dates rejected (consecutive days must differ by exactly 1 calendar day).
    """
    if isinstance(source, str):
        # Determine if source is raw CSV content or a file path
        if "\n" in source or "," in source and not source.endswith(".csv"):
            f = StringIO(source)
            close_f = False
        else:
            f = open(source, mode="r", encoding="utf-8")
            close_f = True
    else:
        f = source
        close_f = False

    try:
        reader = csv.reader(f)
        try:
            raw_header = next(reader)
        except StopIteration:
            raise ValidationError("CSV is completely empty")

        header = [col.strip().lower() for col in raw_header]
        missing = REQUIRED_COLUMNS - set(header)
        if missing:
            raise ValidationError(f"CSV header missing required columns: {sorted(list(missing))}")

        col_map = {col: idx for idx, col in enumerate(header)}

        candles: List[Candle] = []
        prev_date: Union[date, None] = None

        for row_idx, row in enumerate(reader, start=2):
            # Skip empty lines
            if not row or all(c.strip() == "" for c in row):
                continue

            if len(row) < len(header):
                raise ValidationError(f"Row {row_idx}: incomplete columns (expected {len(header)}, got {len(row)})")

            # Parse fields
            try:
                candle_date = parse_date_str(row[col_map["time"]], row_idx)
            except Exception as e:
                if isinstance(e, ValidationError):
                    raise
                raise ValidationError(f"Row {row_idx}: failed parsing date: {e}")

            try:
                open_p = float(row[col_map["open"]].strip())
            except ValueError:
                raise ValidationError(f"Row {row_idx}: invalid open price '{row[col_map['open']]}'")

            try:
                high_p = float(row[col_map["high"]].strip())
            except ValueError:
                raise ValidationError(f"Row {row_idx}: invalid high price '{row[col_map['high']]}'")

            try:
                low_p = float(row[col_map["low"]].strip())
            except ValueError:
                raise ValidationError(f"Row {row_idx}: invalid low price '{row[col_map['low']]}'")

            try:
                close_p = float(row[col_map["close"]].strip())
            except ValueError:
                raise ValidationError(f"Row {row_idx}: invalid close price '{row[col_map['close']]}'")

            try:
                volume = float(row[col_map["volume"]].strip())
            except ValueError:
                raise ValidationError(f"Row {row_idx}: invalid volume '{row[col_map['volume']]}'")

            # Chronological and gap checks
            if prev_date is not None:
                if candle_date == prev_date:
                    raise ValidationError(f"Row {row_idx}: duplicate daily date {candle_date}")
                if candle_date < prev_date:
                    raise ValidationError(
                        f"Row {row_idx}: out-of-order date {candle_date} is before previous date {prev_date}"
                    )
                gap_days = (candle_date - prev_date).days
                if gap_days > 1:
                    raise ValidationError(
                        f"Row {row_idx}: missing daily date gap between {prev_date} and {candle_date} ({gap_days} days difference)"
                    )

            # Candle instantiation enforces candle sanity (high>=low, open/close bounds, positive prices, volume>=0)
            candle = Candle(
                time=candle_date,
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=volume,
            )
            candles.append(candle)
            prev_date = candle_date

        if not candles:
            raise ValidationError("CSV contains no valid candle rows")

        return candles
    finally:
        if close_f:
            f.close()

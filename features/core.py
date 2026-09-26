"""Deterministic, event-time-bounded market feature calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Iterable, Literal, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ATRMethod = Literal["sma", "wilder"]


@dataclass(frozen=True)
class Bar:
    """A completed bar whose timestamp is when its observations became available."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    instrument: str = "NIFTY 50"
    volume_source: Optional[str] = None


@dataclass(frozen=True)
class FeatureValue:
    value: Optional[float]
    available: bool
    reason: Optional[str] = None

    @classmethod
    def unavailable(cls, reason: str) -> "FeatureValue":
        return cls(value=None, available=False, reason=reason)


@dataclass(frozen=True)
class OpeningRange:
    high: Optional[float]
    low: Optional[float]
    width: Optional[float]
    available_at: Optional[datetime]
    available: bool
    reason: Optional[str] = None

    @classmethod
    def unavailable(cls, reason: str) -> "OpeningRange":
        return cls(None, None, None, None, False, reason)


def _valid_timestamp(timestamp: datetime) -> bool:
    return timestamp.tzinfo is not None and timestamp.utcoffset() is not None


def _valid_bar(bar: Bar) -> bool:
    values = (bar.open, bar.high, bar.low, bar.close)
    return (
        _valid_timestamp(bar.timestamp)
        and all(isfinite(value) for value in values)
        and bar.high >= max(bar.open, bar.close, bar.low)
        and bar.low <= min(bar.open, bar.close, bar.high)
        and (bar.volume is None or (isfinite(bar.volume) and bar.volume >= 0))
    )


def _available_bars(bars: Iterable[Bar], event_timestamp: datetime) -> list[Bar]:
    available = [bar for bar in bars if bar.timestamp <= event_timestamp]
    available.sort(key=lambda bar: bar.timestamp)
    if any(not _valid_bar(bar) for bar in available):
        return []
    if any(left.timestamp == right.timestamp for left, right in zip(available, available[1:])):
        return []
    return available


def _opening_range_timestamps(session_date: date) -> tuple[datetime, datetime, datetime]:
    return tuple(
        datetime.combine(session_date, closing_time, tzinfo=IST)
        for closing_time in (time(9, 20), time(9, 25), time(9, 30))
    )


def calculate_opening_range(
    bars: Iterable[Bar], session_date: date, event_timestamp: datetime
) -> OpeningRange:
    """Calculate OR from exactly the three completed 5-minute bars."""

    if not _valid_timestamp(event_timestamp):
        return OpeningRange.unavailable("event_timestamp_timezone_missing")
    expected = _opening_range_timestamps(session_date)
    if event_timestamp < expected[-1]:
        return OpeningRange.unavailable("opening_range_incomplete")

    available = _available_bars(bars, event_timestamp)
    selected = {bar.timestamp: bar for bar in available if bar.timestamp in expected}
    if len(selected) != len(expected):
        return OpeningRange.unavailable("opening_range_bar_missing_or_invalid")

    range_bars = [selected[timestamp] for timestamp in expected]
    high = max(bar.high for bar in range_bars)
    low = min(bar.low for bar in range_bars)
    return OpeningRange(high, low, high - low, expected[-1], True)


def calculate_true_range(previous: Bar, current: Bar) -> FeatureValue:
    if not _valid_bar(previous) or not _valid_bar(current):
        return FeatureValue.unavailable("invalid_bar")
    value = max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )
    return FeatureValue(value, True)


def calculate_atr(
    bars: Iterable[Bar],
    event_timestamp: datetime,
    lookback: int,
    method: ATRMethod,
) -> FeatureValue:
    """Calculate SMA or Wilder ATR without using bars after event_timestamp."""

    if lookback <= 0:
        return FeatureValue.unavailable("atr_lookback_invalid")
    if method not in ("sma", "wilder"):
        return FeatureValue.unavailable("atr_method_unsupported")
    available = _available_bars(bars, event_timestamp)
    if len(available) < lookback + 1:
        return FeatureValue.unavailable("atr_warmup_incomplete")

    true_ranges = [
        calculate_true_range(previous, current).value
        for previous, current in zip(available, available[1:])
    ]
    if any(value is None for value in true_ranges) or len(true_ranges) < lookback:
        return FeatureValue.unavailable("atr_true_range_unavailable")
    numeric_ranges = [value for value in true_ranges if value is not None]
    if method == "sma":
        value = sum(numeric_ranges[-lookback:]) / lookback
    else:
        value = sum(numeric_ranges[:lookback]) / lookback
        for true_range in numeric_ranges[lookback:]:
            value = ((value * (lookback - 1)) + true_range) / lookback
    if not isfinite(value) or value <= 0:
        return FeatureValue.unavailable("atr_zero_or_invalid")
    return FeatureValue(value, True)


def calculate_or_atr(opening_range: OpeningRange, atr: FeatureValue) -> FeatureValue:
    if not opening_range.available or opening_range.width is None:
        return FeatureValue.unavailable("opening_range_unavailable")
    if not atr.available or atr.value is None or atr.value <= 0:
        return FeatureValue.unavailable("atr_unavailable")
    value = opening_range.width / atr.value
    return FeatureValue(value, isfinite(value), None if isfinite(value) else "or_atr_invalid")


def calculate_vwap(
    price_bars: Iterable[Bar],
    volume_bars: Iterable[Bar],
    session_date: date,
    event_timestamp: datetime,
    volume_source: Optional[str],
    session_start: time,
    price_field: str = "close",
) -> FeatureValue:
    """Calculate session VWAP only when the volume source is explicit and valid."""

    if volume_source is None:
        return FeatureValue.unavailable("vwap_volume_source_unspecified")
    if price_field not in {"open", "high", "low", "close"}:
        return FeatureValue.unavailable("vwap_price_field_unsupported")
    prices = []
    for bar in _available_bars(price_bars, event_timestamp):
        local_timestamp = bar.timestamp.astimezone(IST)
        if local_timestamp.date() == session_date and local_timestamp.time() >= session_start:
            prices.append(bar)
    volumes = {
        bar.timestamp: bar
        for bar in _available_bars(volume_bars, event_timestamp)
        if bar.timestamp.astimezone(IST).date() == session_date
        and bar.timestamp.astimezone(IST).time() >= session_start
    }
    if not prices:
        return FeatureValue.unavailable("vwap_price_data_missing")
    if any(bar.timestamp not in volumes for bar in prices):
        return FeatureValue.unavailable("vwap_volume_data_missing")
    if any(volumes[bar.timestamp].volume_source != volume_source for bar in prices):
        return FeatureValue.unavailable("vwap_volume_source_mismatch")

    weighted_total = 0.0
    volume_total = 0.0
    for price_bar in prices:
        volume_bar = volumes[price_bar.timestamp]
        if volume_bar.volume is None or not isfinite(volume_bar.volume) or volume_bar.volume < 0:
            return FeatureValue.unavailable("vwap_volume_invalid")
        weighted_total += getattr(price_bar, price_field) * volume_bar.volume
        volume_total += volume_bar.volume
    if volume_total <= 0:
        return FeatureValue.unavailable("vwap_volume_zero")
    return FeatureValue(weighted_total / volume_total, True)
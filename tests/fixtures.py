"""Deterministic synthetic market and option fixtures for V0.1 tests."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from features import Bar
from strategy.models import OptionCandidate, OptionPriceSnapshot, UnderlyingReference

IST = ZoneInfo("Asia/Kolkata")
SESSION_DATE = date(2026, 1, 5)
EXPIRY = date(2026, 1, 8)


def timestamp(hour: int, minute: int, session_date: date = SESSION_DATE) -> datetime:
    return datetime.combine(session_date, time(hour, minute), tzinfo=IST)


def market_bars(final_close: float = 110.0, session_date: date = SESSION_DATE) -> list[Bar]:
    values = [
        (9, 0, 99.0, 101.0, 98.0, 100.0),
        (9, 5, 100.0, 102.0, 99.0, 101.0),
        (9, 10, 101.0, 103.0, 100.0, 102.0),
        (9, 20, 102.0, 104.0, 101.0, 103.0),
        (9, 25, 103.0, 105.0, 102.0, 104.0),
        (9, 30, 104.0, 106.0, 103.0, 105.0),
        (9, 35, 105.0, max(final_close, 106.0), min(final_close, 104.0), final_close),
    ]
    return [
        Bar(timestamp(h, m, session_date), open_, high, low, close, volume=100.0, volume_source="nifty_futures")
        for h, m, open_, high, low, close in values
    ]


def volume_bars(bars: Iterable[Bar]) -> list[Bar]:
    return [
        Bar(
            bar.timestamp,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            volume=bar.volume,
            instrument="NIFTY Futures",
            volume_source="nifty_futures",
        )
        for bar in bars
    ]


def option_candidates(
    direction: str = "CE", timestamp_value: datetime | None = None, session_date: date = SESSION_DATE
) -> list[OptionCandidate]:
    event = timestamp_value or timestamp(9, 35, session_date)
    strikes = (100.0, 105.0, 110.0) if direction == "CE" else (100.0, 95.0, 90.0)
    return [
        OptionCandidate(
            underlying="NIFTY 50",
            expiry=EXPIRY,
            strike=strike,
            right=direction,
            timestamp=event,
            available_at=event,
            source="test_option_feed",
            premium=10.0 - rank,
            delta=0.50 if direction == "CE" else -0.50,
            gamma=0.01,
            volume=1000.0,
            oi=5000.0,
            distance_from_atm=abs(strike - 100.0),
            time_to_expiry=datetime.combine(EXPIRY, time.min, tzinfo=IST) - event,
        )
        for rank, strike in enumerate(strikes)
    ]


def score_values(**overrides: float | None) -> dict[str, float | None]:
    values: dict[str, float | None] = {
        "compression": 1.0,
        "breakout": 1.0,
        "vwap": 1.0,
        "volume": 1.0,
        "trend": 1.0,
        "key_level": 1.0,
        "option_quality": 1.0,
    }
    values.update(overrides)
    return values

def underlying_reference(event: datetime, price: float = 100.0) -> UnderlyingReference:
    return UnderlyingReference(
        instrument="NIFTY 50", timestamp=event, available_at=event, price=price, source="test_spot_feed"
    )

def option_price_snapshot(event: datetime, price: float = 10.0, direction: str = "CE", strike: float = 100.0) -> OptionPriceSnapshot:
    return OptionPriceSnapshot(
        contract_identity=("NIFTY 50", EXPIRY, strike, direction),
        timestamp=event, available_at=event, price=price, source="test_option_feed"
    )

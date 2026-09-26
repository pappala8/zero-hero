"""Fail-closed validation for provider-neutral real-time market events."""
from __future__ import annotations
from datetime import datetime
from math import isfinite
from .models import RealtimeRawEvent, RealtimeMarketEvent, RealtimeInstrument

_VALID_RIGHTS = {"CE", "PE"}

def _finite_nonnegative(value: float | None, name: str, allow_none: bool = True) -> None:
    if value is None and allow_none:
        return
    if value is None or not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")

def _validate_times(event_timestamp: datetime, available_at: datetime) -> None:
    if event_timestamp.tzinfo is None or available_at.tzinfo is None:
        raise ValueError("event_timestamp and available_at must be timezone-aware")
    if available_at < event_timestamp:
        raise ValueError("available_at cannot precede event_timestamp")

def _validate_common(provider: str, source_event_id: str, instrument_token: int, underlying: str) -> None:
    if not provider.strip(): raise ValueError("provider is required")
    if not source_event_id.strip(): raise ValueError("source_event_id is required")
    if not isinstance(instrument_token, int) or instrument_token <= 0:
        raise ValueError("instrument_token must be a positive integer")
    if underlying.strip().upper() != "NIFTY":
        raise ValueError("P2-A currently supports NIFTY only")

def _validate_instrument(instrument: RealtimeInstrument, expiry, strike, right) -> None:
    if instrument is RealtimeInstrument.SPOT:
        if any(x is not None for x in (expiry, strike, right)):
            raise ValueError("spot cannot have option/future contract fields")
    elif instrument is RealtimeInstrument.OPTION:
        if expiry is None or strike is None or right not in _VALID_RIGHTS:
            raise ValueError("option requires expiry, strike and CE/PE right")
        if not isfinite(strike) or strike <= 0:
            raise ValueError("option strike must be positive and finite")
    elif instrument is RealtimeInstrument.FUTURE:
        if expiry is None or strike is not None or right is not None:
            raise ValueError("future requires expiry and no strike/right")
    else:
        raise ValueError("unsupported instrument")

def _validate_ohlc(open_, high, low, close) -> None:
    vals = (open_, high, low, close)
    if any(v is None for v in vals): return
    if not all(isfinite(v) and v >= 0 for v in vals):
        raise ValueError("OHLC must be finite and non-negative")
    if high < max(open_, close) or low > min(open_, close) or high < low:
        raise ValueError("invalid OHLC relationship")

def validate_raw_event(event: RealtimeRawEvent) -> RealtimeRawEvent:
    _validate_common(event.provider, event.source_event_id, event.instrument_token, event.underlying)
    _validate_times(event.event_timestamp, event.available_at)
    _validate_instrument(event.instrument, event.expiry, event.strike, event.right)
    _validate_ohlc(event.open, event.high, event.low, event.close)
    _finite_nonnegative(event.volume, "volume")
    _finite_nonnegative(event.open_interest, "open_interest")
    if event.source_sequence is not None and (not isinstance(event.source_sequence, int) or event.source_sequence < 0):
        raise ValueError("source_sequence must be a non-negative integer")
    return event

def validate_market_event(event: RealtimeMarketEvent) -> RealtimeMarketEvent:
    _validate_common(event.provider, event.source_event_id, event.instrument_token, event.contract.underlying)
    _validate_times(event.event_timestamp, event.available_at)
    _validate_instrument(event.instrument, event.contract.expiry, event.contract.strike, event.contract.right)
    _validate_ohlc(event.open, event.high, event.low, event.close)
    _finite_nonnegative(event.volume, "volume")
    _finite_nonnegative(event.open_interest, "open_interest")
    return event

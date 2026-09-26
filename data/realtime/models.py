"""Immutable provider-neutral real-time market-data contracts."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from typing import Optional

from data.models import ContractIdentity, InstrumentKind

class RealtimeInstrument(str, Enum):
    SPOT = InstrumentKind.SPOT.value
    OPTION = InstrumentKind.OPTION.value
    FUTURE = InstrumentKind.FUTURE.value

@dataclass(frozen=True, slots=True)
class RealtimeRawEvent:
    provider: str
    source_event_id: str
    event_timestamp: datetime
    available_at: datetime
    instrument_token: int
    instrument: RealtimeInstrument
    underlying: str
    expiry: Optional[date] = None
    strike: Optional[float] = None
    right: Optional[str] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    source_sequence: Optional[int] = None

    @property
    def contract(self) -> ContractIdentity:
        return ContractIdentity(self.underlying, self.expiry, self.strike, self.right)

@dataclass(frozen=True, slots=True)
class RealtimeMarketEvent:
    provider: str
    source_event_id: str
    event_timestamp: datetime
    available_at: datetime
    instrument_token: int
    instrument: RealtimeInstrument
    contract: ContractIdentity
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    source_sequence: Optional[int] = None

    @property
    def identity_key(self) -> tuple[object, ...]:
        return (self.event_timestamp, self.instrument.value, self.contract.as_tuple())

"""Immutable provider-neutral raw and normalized market-data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from math import isfinite
from typing import Mapping, Optional


class InstrumentKind(str, Enum):
    SPOT = "SPOT"
    OPTION = "OPTION"
    FUTURE = "FUTURE"


class TradingDayKind(str, Enum):
    EXPECTED = "EXPECTED"
    HOLIDAY = "HOLIDAY"
    SPECIAL_SESSION = "SPECIAL_SESSION"
    PROVIDER_OUTAGE = "PROVIDER_OUTAGE"
    UNEXPLAINED_MISSING = "UNEXPLAINED_MISSING"


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    underlying: str
    expiry: Optional[date] = None
    strike: Optional[float] = None
    right: Optional[str] = None

    def as_tuple(self) -> tuple[str, Optional[date], Optional[float], Optional[str]]:
        return self.underlying, self.expiry, self.strike, self.right


@dataclass(frozen=True, slots=True)
class SessionInfo:
    session_date: date
    timezone: str
    session_open: time
    session_close: time
    calendar_id: str
    day_kind: TradingDayKind = TradingDayKind.EXPECTED
    is_special_session: bool = False


@dataclass(frozen=True, slots=True)
class RawReleaseMetadata:
    release_id: str
    source: str
    retrieval_timestamp: datetime
    schema_version: str
    license_reference: Optional[str] = None
    checksum: Optional[str] = None
    file_size: Optional[int] = None
    partition_id: Optional[str] = None
    source_metadata: Mapping[str, str] = None


@dataclass(frozen=True, slots=True)
class RawMarketRecord:
    release: RawReleaseMetadata
    source_record_id: str
    instrument: InstrumentKind
    contract: ContractIdentity
    timestamp: datetime
    session: SessionInfo
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float] = None
    open_interest: Optional[float] = None


@dataclass(frozen=True, slots=True)
class NormalizedBar:
    normalization_version: str
    raw_release_id: str
    source_record_id: str
    instrument: InstrumentKind
    contract: ContractIdentity
    event_timestamp: datetime
    session: SessionInfo
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float] = None
    open_interest: Optional[float] = None

    @property
    def identity_key(self) -> tuple[object, ...]:
        return self.event_timestamp, self.instrument, self.contract.as_tuple()

    @property
    def has_finite_prices(self) -> bool:
        values = (self.open, self.high, self.low, self.close)
        return all(value is not None and isfinite(value) for value in values)
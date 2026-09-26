"""Typed strategy inputs and deterministic signal outputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from math import isfinite
from typing import Mapping, Optional


class Mode(str, Enum):
    DAILY = "DAILY_ZH"
    EXPIRY = "EXPIRY_ZH"
    NO_TRADE = "NO_TRADE_CAPITAL_PROTECTION"


class Direction(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class SignalState(str, Enum):
    NO_TRADE = "NO_TRADE"
    REVIEW = "REVIEW"
    BUY_CALL = "BUY_CALL"
    BUY_PUT = "BUY_PUT"


@dataclass(frozen=True)
class UnderlyingReference:
    """Causal underlying snapshot used for ATM mapping and signal context."""

    instrument: str
    timestamp: datetime
    available_at: datetime
    price: float
    source: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("underlying reference timestamp must include a timezone")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise ValueError("underlying reference availability timestamp must include a timezone")
        if self.available_at < self.timestamp:
            raise ValueError("underlying reference available_at cannot precede timestamp")
        if not self.instrument or not self.source:
            raise ValueError("underlying reference instrument and source are required")
        if not isfinite(self.price):
            raise ValueError("underlying reference price must be finite")


@dataclass(frozen=True)
class OptionPriceSnapshot:
    """Causal executable-price observation for a specific option contract."""

    contract_identity: tuple[str, date, float, str]
    timestamp: datetime
    available_at: datetime
    price: float
    source: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("option price timestamp must include a timezone")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise ValueError("option price available_at must include a timezone")
        if self.available_at < self.timestamp:
            raise ValueError("option price available_at cannot precede timestamp")
        if not self.source:
            raise ValueError("option price source is required")


@dataclass(frozen=True)
class OptionCandidate:
    underlying: str
    expiry: date
    strike: float
    right: str
    timestamp: datetime
    available_at: datetime
    source: str
    premium: Optional[float]
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    spread: Optional[float] = None
    volume: Optional[float] = None
    oi: Optional[float] = None
    distance_from_atm: Optional[float] = None
    time_to_expiry: Optional[timedelta] = None

    def __post_init__(self) -> None:
        if self.right not in {"CE", "PE"}:
            raise ValueError("option right must be CE or PE")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("option timestamp must include a timezone")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise ValueError("option available_at must include a timezone")
        if self.available_at < self.timestamp:
            raise ValueError("option available_at cannot precede timestamp")
        if not self.source:
            raise ValueError("option source is required")
        if not isfinite(self.strike) or self.strike <= 0:
            raise ValueError("option strike must be positive and finite")

    @property
    def contract_identity(self) -> tuple[str, date, float, str]:
        return self.underlying, self.expiry, self.strike, self.right

    def has_required_fields(self, fields: tuple[str, ...]) -> bool:
        return all(getattr(self, field, None) is not None for field in fields)


@dataclass(frozen=True)
class ScoreComponents:
    compression: Optional[float]
    breakout: Optional[float]
    vwap: Optional[float]
    volume: Optional[float]
    trend: Optional[float]
    key_level: Optional[float]
    option_quality: Optional[float]

    NAMES = (
        "compression",
        "breakout",
        "vwap",
        "volume",
        "trend",
        "key_level",
        "option_quality",
    )
    WEIGHTS = {
        "compression": 20,
        "breakout": 20,
        "vwap": 15,
        "volume": 15,
        "trend": 10,
        "key_level": 10,
        "option_quality": 10,
    }

    @classmethod
    def from_mapping(cls, values: Mapping[str, Optional[float]]) -> "ScoreComponents":
        return cls(*(values.get(name) for name in cls.NAMES))

    def as_dict(self) -> dict[str, Optional[float]]:
        return {name: getattr(self, name) for name in self.NAMES}

    def score(self) -> Optional[float]:
        values = self.as_dict()
        if any(value is None or not isfinite(value) or not 0 <= value <= 1 for value in values.values()):
            return None
        return sum(self.WEIGHTS[name] * values[name] for name in self.NAMES)


@dataclass(frozen=True)
class Signal:
    timestamp: datetime
    mode: Mode
    direction: Optional[Direction]
    signal_state: SignalState
    or_high: Optional[float]
    or_low: Optional[float]
    or_width: Optional[float]
    atr: Optional[float]
    or_atr: Optional[float]
    vwap: Optional[float]
    zh_score: Optional[float]
    score_components: ScoreComponents
    candidate_contracts: tuple[OptionCandidate, ...]
    selected_contract: Optional[OptionCandidate]
    rejection_reasons: tuple[str, ...]
    feature_config_version: str
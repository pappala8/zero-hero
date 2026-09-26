"""Deterministic paper session ledger and event-ingestion boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Iterable, Iterator, Optional, Protocol
from zoneinfo import ZoneInfo

from .paper import PaperBarEvent, PaperEventResult, PaperTradingEngine, PositionState

IST = ZoneInfo("Asia/Kolkata")


class PaperEventSource(Protocol):
    def __iter__(self) -> Iterator[PaperBarEvent]:
        """Yield paper events in chronological order."""


@dataclass(frozen=True)
class IterablePaperEventSource:
    events: tuple[PaperBarEvent, ...]

    def __iter__(self) -> Iterator[PaperBarEvent]:
        return iter(self.events)


@dataclass(frozen=True)
class AccountSnapshot:
    session_date: Optional[date]
    starting_balance: float
    balance: float
    equity: Optional[float]
    realized_pnl: float
    unrealized_pnl: Optional[float]
    daily_pnl: float
    trades_today: int
    consecutive_losses: int
    position_state: PositionState
    session_closed: bool


@dataclass(frozen=True)
class LedgerEventResult:
    accepted: bool
    event: PaperBarEvent
    event_result: Optional[PaperEventResult]
    snapshot: AccountSnapshot
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SessionCloseResult:
    accepted: bool
    session_date: date
    close_timestamp: datetime
    snapshot: AccountSnapshot
    reasons: tuple[str, ...]


@dataclass
class PaperAccountLedger:
    """Own account/session state while delegating strategy and fills to the paper engine."""

    engine: PaperTradingEngine
    starting_balance: float
    current_session_date: Optional[date] = None
    last_event_timestamp: Optional[datetime] = None
    closed_session_date: Optional[date] = None

    def __post_init__(self) -> None:
        if not isfinite(self.starting_balance) or self.starting_balance <= 0:
            raise ValueError("starting_balance must be finite and positive")

    @property
    def realized_pnl(self) -> float:
        return sum(record.net_pnl for record in self.engine.trade_records)

    @property
    def balance(self) -> float:
        return self.starting_balance + self.realized_pnl

    def snapshot(self, event: Optional[PaperBarEvent] = None) -> AccountSnapshot:
        unrealized = None
        if self.engine.position is not None and event is not None and event.current_option_price is not None:
            unrealized = (
                event.current_option_price.price - self.engine.position.entry_price
            ) * self.engine.position.quantity
        equity = None if unrealized is None else self.balance + unrealized
        if self.engine.position is None:
            equity = self.balance
        return AccountSnapshot(
            session_date=self.current_session_date,
            starting_balance=self.starting_balance,
            balance=self.balance,
            equity=equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            daily_pnl=self.engine.daily_pnl,
            trades_today=self.engine.trades_today,
            consecutive_losses=self.engine.consecutive_losses,
            position_state=self.engine.state,
            session_closed=self.closed_session_date == self.current_session_date,
        )

    def ingest(self, event: PaperBarEvent) -> LedgerEventResult:
        validation_reasons = self._validate_event(event)
        if validation_reasons:
            return LedgerEventResult(False, event, None, self.snapshot(), validation_reasons)
        if self.last_event_timestamp is not None:
            if event.timestamp == self.last_event_timestamp:
                return LedgerEventResult(False, event, None, self.snapshot(), ("duplicate_event_timestamp",))
            if event.timestamp < self.last_event_timestamp:
                return LedgerEventResult(False, event, None, self.snapshot(), ("out_of_order_event_timestamp",))
        if self.current_session_date is None:
            self.current_session_date = event.session_date
        elif event.session_date != self.current_session_date:
            if self.engine.position is not None:
                return LedgerEventResult(
                    False,
                    event,
                    None,
                    self.snapshot(),
                    ("open_position_crosses_session_boundary",),
                )
            self.engine.reset_daily_state()
            self.current_session_date = event.session_date
            self.closed_session_date = None
        elif self.closed_session_date == event.session_date:
            return LedgerEventResult(False, event, None, self.snapshot(), ("session_already_closed",))

        result = self.engine.process(
            event,
            capital_available=self.balance,
            equity=self._equity_for_risk(event),
        )
        self.last_event_timestamp = event.timestamp
        return LedgerEventResult(True, event, result, self.snapshot(event), result.reasons)

    def close_session(self, session_date: date, close_timestamp: datetime) -> SessionCloseResult:
        if close_timestamp.tzinfo is None or close_timestamp.utcoffset() is None:
            return SessionCloseResult(
                False, session_date, close_timestamp, self.snapshot(), ("close_timestamp_timezone_missing",)
            )
        if close_timestamp.astimezone(IST).date() != session_date:
            return SessionCloseResult(
                False, session_date, close_timestamp, self.snapshot(), ("close_session_date_mismatch",)
            )
        if self.current_session_date != session_date:
            return SessionCloseResult(
                False, session_date, close_timestamp, self.snapshot(), ("session_not_active",)
            )
        if self.closed_session_date == session_date:
            return SessionCloseResult(
                False, session_date, close_timestamp, self.snapshot(), ("session_already_closed",)
            )
        if close_timestamp.astimezone(IST).time() < self.engine.config.forced_eod_time:
            return SessionCloseResult(
                False, session_date, close_timestamp, self.snapshot(), ("session_close_before_forced_eod",)
            )
        if self.engine.position is not None:
            return SessionCloseResult(
                False, session_date, close_timestamp, self.snapshot(), ("open_position_requires_forced_eod_event",)
            )
        self.closed_session_date = session_date
        return SessionCloseResult(True, session_date, close_timestamp, self.snapshot(), ())

    def run(self, source: PaperEventSource) -> tuple[LedgerEventResult, ...]:
        return tuple(self.ingest(event) for event in source)

    def _equity_for_risk(self, event: PaperBarEvent) -> Optional[float]:
        if self.engine.position is None:
            return self.balance
        return self.snapshot(event).equity

    @staticmethod
    def _validate_event(event: PaperBarEvent) -> tuple[str, ...]:
        if event.timestamp.tzinfo is None or event.timestamp.utcoffset() is None:
            return ("event_timestamp_timezone_missing",)
        if event.timestamp.astimezone(IST).date() != event.session_date:
            return ("event_session_date_mismatch",)
        if not event.underlying_bars:
            return ("underlying_bar_data_missing",)
        if event.current_option_price is not None:
            price = event.current_option_price
            if not isfinite(price.price) or price.price <= 0:
                return ("current_option_price_invalid",)
            if price.timestamp > event.timestamp or price.available_at > event.timestamp:
                return ("current_option_price_not_causal",)
            if not price.source:
                return ("current_option_price_source_missing",)
        if event.underlying_reference is not None:
            reference = event.underlying_reference
            if reference.timestamp > event.timestamp or reference.available_at > event.timestamp:
                return ("underlying_reference_not_causal",)
        if event.lot_size is not None and event.lot_size <= 0:
            return ("lot_size_invalid",)
        if event.stop_risk_per_unit is not None and (
            not isfinite(event.stop_risk_per_unit) or event.stop_risk_per_unit <= 0
        ):
            return ("stop_risk_per_unit_invalid",)
        return ()
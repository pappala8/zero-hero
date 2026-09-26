"""Append-only deterministic paper-event journal and replay support."""

from __future__ import annotations

import json
import hashlib
import copy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from features import Bar
from strategy import Mode, OptionCandidate, OptionPriceSnapshot, UnderlyingReference

from .ledger import (
    AccountSnapshot,
    LedgerEventResult,
    PaperAccountLedger,
    SessionCloseResult,
)
from .paper import PaperBarEvent, PositionState


def _encode_datetime(value: datetime) -> str:
    return value.isoformat()


def _decode_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _encode_bar(bar: Bar) -> dict[str, Any]:
    return {
        "timestamp": _encode_datetime(bar.timestamp),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "instrument": bar.instrument,
        "volume_source": bar.volume_source,
    }


def _decode_bar(value: dict[str, Any]) -> Bar:
    return Bar(
        timestamp=_decode_datetime(value["timestamp"]),
        open=value["open"],
        high=value["high"],
        low=value["low"],
        close=value["close"],
        volume=value["volume"],
        instrument=value["instrument"],
        volume_source=value["volume_source"],
    )


def _encode_candidate(candidate: OptionCandidate) -> dict[str, Any]:
    return {
        "underlying": candidate.underlying,
        "expiry": candidate.expiry.isoformat(),
        "strike": candidate.strike,
        "right": candidate.right,
        "timestamp": _encode_datetime(candidate.timestamp),
        "premium": candidate.premium,
        "delta": candidate.delta,
        "gamma": candidate.gamma,
        "theta": candidate.theta,
        "vega": candidate.vega,
        "spread": candidate.spread,
        "volume": candidate.volume,
        "oi": candidate.oi,
        "distance_from_atm": candidate.distance_from_atm,
        "available_at": _encode_datetime(candidate.available_at),
        "source": candidate.source,
        "time_to_expiry_seconds": (
            candidate.time_to_expiry.total_seconds() if candidate.time_to_expiry is not None else None
        ),
    }


def _decode_candidate(value: dict[str, Any]) -> OptionCandidate:
    time_to_expiry = value["time_to_expiry_seconds"]
    return OptionCandidate(
        underlying=value["underlying"],
        expiry=date.fromisoformat(value["expiry"]),
        strike=value["strike"],
        right=value["right"],
        timestamp=_decode_datetime(value["timestamp"]),
        available_at=_decode_datetime(value.get("available_at", value["timestamp"])),
        source=value.get("source", "legacy_journal"),
        premium=value["premium"],
        delta=value["delta"],
        gamma=value["gamma"],
        theta=value["theta"],
        vega=value["vega"],
        spread=value["spread"],
        volume=value["volume"],
        oi=value["oi"],
        distance_from_atm=value["distance_from_atm"],
        time_to_expiry=timedelta(seconds=time_to_expiry) if time_to_expiry is not None else None,
    )


def _encode_underlying_reference(value: UnderlyingReference | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "instrument": value.instrument,
        "timestamp": _encode_datetime(value.timestamp),
        "available_at": _encode_datetime(value.available_at),
        "price": value.price,
        "source": value.source,
    }


def _decode_underlying_reference(value: dict[str, Any] | None) -> UnderlyingReference | None:
    if value is None:
        return None
    return UnderlyingReference(
        instrument=value["instrument"],
        timestamp=_decode_datetime(value["timestamp"]),
        available_at=_decode_datetime(value["available_at"]),
        price=value["price"],
        source=value["source"],
    )


def _encode_option_price(value: OptionPriceSnapshot | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "contract_identity": [value.contract_identity[0], value.contract_identity[1].isoformat(), value.contract_identity[2], value.contract_identity[3]],
        "timestamp": _encode_datetime(value.timestamp),
        "available_at": _encode_datetime(value.available_at),
        "price": value.price,
        "source": value.source,
    }


def _decode_option_price(value: dict[str, Any] | None) -> OptionPriceSnapshot | None:
    if value is None:
        return None
    identity = value["contract_identity"]
    return OptionPriceSnapshot(
        contract_identity=(identity[0], date.fromisoformat(identity[1]), float(identity[2]), identity[3]),
        timestamp=_decode_datetime(value["timestamp"]),
        available_at=_decode_datetime(value["available_at"]),
        price=value["price"],
        source=value["source"],
    )


class PaperEventCodec:
    @staticmethod
    def encode(event: PaperBarEvent) -> dict[str, Any]:
        return {
            "timestamp": _encode_datetime(event.timestamp),
            "session_date": event.session_date.isoformat(),
            "mode": event.mode.value,
            "underlying_bars": [_encode_bar(bar) for bar in event.underlying_bars],
            "volume_bars": [_encode_bar(bar) for bar in event.volume_bars],
            "score_components": dict(sorted(event.score_components.items())),
            "option_candidates": [_encode_candidate(candidate) for candidate in event.option_candidates],
            "selected_expiry": event.selected_expiry.isoformat(),
            "underlying_reference": _encode_underlying_reference(event.underlying_reference),
            "current_option_price": _encode_option_price(event.current_option_price),
            "lot_size": event.lot_size,
            "stop_risk_per_unit": event.stop_risk_per_unit,
            "structural_stop_triggered": event.structural_stop_triggered,
        }

    @staticmethod
    def decode(value: dict[str, Any]) -> PaperBarEvent:
        return PaperBarEvent(
            timestamp=_decode_datetime(value["timestamp"]),
            session_date=date.fromisoformat(value["session_date"]),
            mode=Mode(value["mode"]),
            underlying_bars=tuple(_decode_bar(bar) for bar in value["underlying_bars"]),
            volume_bars=tuple(_decode_bar(bar) for bar in value["volume_bars"]),
            score_components=value["score_components"],
            option_candidates=tuple(_decode_candidate(candidate) for candidate in value["option_candidates"]),
            selected_expiry=date.fromisoformat(value["selected_expiry"]),
            underlying_reference=_decode_underlying_reference(value.get("underlying_reference")),
            current_option_price=_decode_option_price(value.get("current_option_price")),
            lot_size=value["lot_size"],
            stop_risk_per_unit=value["stop_risk_per_unit"],
            structural_stop_triggered=value["structural_stop_triggered"],
        )


def _encode_snapshot(snapshot: AccountSnapshot) -> dict[str, Any]:
    return {
        "session_date": snapshot.session_date.isoformat() if snapshot.session_date else None,
        "starting_balance": snapshot.starting_balance,
        "balance": snapshot.balance,
        "equity": snapshot.equity,
        "realized_pnl": snapshot.realized_pnl,
        "unrealized_pnl": snapshot.unrealized_pnl,
        "daily_pnl": snapshot.daily_pnl,
        "trades_today": snapshot.trades_today,
        "consecutive_losses": snapshot.consecutive_losses,
        "position_state": snapshot.position_state.value,
        "session_closed": snapshot.session_closed,
    }


@dataclass(frozen=True)
class JournalEntry:
    sequence: int
    operation: str
    timestamp: datetime
    session_date: date
    event: Optional[PaperBarEvent]
    accepted: bool
    reasons: tuple[str, ...]
    snapshot: AccountSnapshot
    previous_hash: str = "0" * 64
    entry_hash: str = ""

    def payload_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "operation": self.operation,
            "timestamp": _encode_datetime(self.timestamp),
            "session_date": self.session_date.isoformat(),
            "event": PaperEventCodec.encode(self.event) if self.event is not None else None,
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "snapshot": _encode_snapshot(self.snapshot),
        }

    def computed_hash(self) -> str:
        payload = dict(self.payload_dict())
        payload["previous_hash"] = self.previous_hash
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        payload = self.payload_dict()
        payload["previous_hash"] = self.previous_hash
        payload["entry_hash"] = self.entry_hash or self.computed_hash()
        return payload


@dataclass
class PaperEventJournal:
    _entries: list[JournalEntry] = field(default_factory=list)

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def append(self, entry: JournalEntry) -> JournalEntry:
        expected = len(self._entries) + 1
        if entry.sequence != expected:
            raise ValueError("journal sequence must be contiguous and append-only")
        previous_hash = self._entries[-1].entry_hash if self._entries else "0" * 64
        if entry.previous_hash != previous_hash:
            raise ValueError("journal hash chain predecessor mismatch")
        expected_hash = entry.computed_hash()
        if entry.entry_hash and entry.entry_hash != expected_hash:
            raise ValueError("journal entry hash mismatch")
        if not entry.entry_hash:
            entry = JournalEntry(**{**entry.__dict__, "entry_hash": expected_hash})
        self._entries.append(entry)
        return entry

    def to_json_lines(self) -> str:
        return "".join(
            json.dumps(entry.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
            for entry in self._entries
        )

    @classmethod
    def from_json_lines(cls, content: str) -> "PaperEventJournal":
        journal = cls()
        for line in content.splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            event_value = value["event"]
            event = PaperEventCodec.decode(event_value) if event_value is not None else None
            snapshot_value = value["snapshot"]
            snapshot = AccountSnapshot(
                session_date=(
                    date.fromisoformat(snapshot_value["session_date"])
                    if snapshot_value["session_date"]
                    else None
                ),
                starting_balance=snapshot_value["starting_balance"],
                balance=snapshot_value["balance"],
                equity=snapshot_value["equity"],
                realized_pnl=snapshot_value["realized_pnl"],
                unrealized_pnl=snapshot_value["unrealized_pnl"],
                daily_pnl=snapshot_value["daily_pnl"],
                trades_today=snapshot_value["trades_today"],
                consecutive_losses=snapshot_value["consecutive_losses"],
                position_state=PositionState(snapshot_value["position_state"]),
                session_closed=snapshot_value["session_closed"],
            )
            journal.append(
                JournalEntry(
                    sequence=value["sequence"],
                    operation=value["operation"],
                    timestamp=_decode_datetime(value["timestamp"]),
                    session_date=date.fromisoformat(value["session_date"]),
                    event=event,
                    accepted=value["accepted"],
                    reasons=tuple(value["reasons"]),
                    snapshot=snapshot,
                    previous_hash=value.get("previous_hash", "0" * 64),
                    entry_hash=value.get("entry_hash", ""),
                )
            )
        return journal


@dataclass(frozen=True)
class ReplayMismatchError(Exception):
    sequence: int
    reason: str


@dataclass
class JournaledPaperSession:
    ledger: PaperAccountLedger
    journal: PaperEventJournal = field(default_factory=PaperEventJournal)

    def ingest(self, event: PaperBarEvent) -> LedgerEventResult:
        before = copy.deepcopy(self.ledger)
        result = self.ledger.ingest(event)
        previous_hash = self.journal.entries[-1].entry_hash if self.journal.entries else "0" * 64
        try:
            self.journal.append(
                JournalEntry(
                    sequence=len(self.journal.entries) + 1,
                    operation="event",
                    timestamp=event.timestamp,
                    session_date=event.session_date,
                    event=event,
                    accepted=result.accepted,
                    reasons=result.reasons,
                    snapshot=result.snapshot,
                    previous_hash=previous_hash,
                )
            )
        except Exception:
            self.ledger = before
            raise
        return result

    def close_session(self, session_date: date, close_timestamp: datetime) -> SessionCloseResult:
        before = copy.deepcopy(self.ledger)
        result = self.ledger.close_session(session_date, close_timestamp)
        previous_hash = self.journal.entries[-1].entry_hash if self.journal.entries else "0" * 64
        try:
            self.journal.append(
                JournalEntry(
                    sequence=len(self.journal.entries) + 1,
                    operation="session_close",
                    timestamp=close_timestamp,
                    session_date=session_date,
                    event=None,
                    accepted=result.accepted,
                    reasons=result.reasons,
                    snapshot=result.snapshot,
                    previous_hash=previous_hash,
                )
            )
        except Exception:
            self.ledger = before
            raise
        return result

    @classmethod
    def replay(
        cls, journal: PaperEventJournal, ledger_factory: Callable[[], PaperAccountLedger]
    ) -> "JournaledPaperSession":
        session = cls(ledger_factory())
        for entry in journal.entries:
            if entry.operation == "event" and entry.event is not None:
                actual = session.ingest(entry.event)
                accepted = actual.accepted
                reasons = actual.reasons
                snapshot = actual.snapshot
            elif entry.operation == "session_close":
                actual = session.close_session(entry.session_date, entry.timestamp)
                accepted = actual.accepted
                reasons = actual.reasons
                snapshot = actual.snapshot
            else:
                raise ReplayMismatchError(entry.sequence, "unknown_journal_operation")
            if accepted != entry.accepted or reasons != entry.reasons or snapshot != entry.snapshot:
                raise ReplayMismatchError(entry.sequence, "replay_result_mismatch")
        return session

    @classmethod
    def recover_json_lines(
        cls, content: str, ledger_factory: Callable[[], PaperAccountLedger]
    ) -> "JournaledPaperSession":
        try:
            journal = PaperEventJournal.from_json_lines(content)
        except ValueError as error:
            raise ReplayMismatchError(0, f"journal_integrity_failure: {error}") from error
        return cls.replay(journal, ledger_factory)
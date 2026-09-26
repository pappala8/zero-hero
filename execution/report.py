"""Deterministic paper-session reporting and reconciliation."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from math import isclose
from typing import Any, Optional

from .journal import JournaledPaperSession
from .storage import JournalManifest


@dataclass(frozen=True)
class PaperSessionReport:
    manifest_hash: str
    journal_schema_version: str
    entry_count: int
    event_count: int
    session_close_count: int
    accepted_operations: int
    rejected_operations: int
    rejected_reason_counts: tuple[tuple[str, int], ...]
    sessions: tuple[str, ...]
    closed_sessions: tuple[str, ...]
    trade_count: int
    gross_pnl: float
    transaction_costs: float
    net_pnl: float
    starting_balance: float
    final_balance: float
    final_equity: Optional[float]
    final_position_state: str
    last_timestamp: Optional[str]
    reconciliation_ok: bool
    reconciliation_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_hash": self.manifest_hash,
            "journal_schema_version": self.journal_schema_version,
            "entry_count": self.entry_count,
            "event_count": self.event_count,
            "session_close_count": self.session_close_count,
            "accepted_operations": self.accepted_operations,
            "rejected_operations": self.rejected_operations,
            "rejected_reason_counts": dict(self.rejected_reason_counts),
            "sessions": list(self.sessions),
            "closed_sessions": list(self.closed_sessions),
            "trade_count": self.trade_count,
            "gross_pnl": self.gross_pnl,
            "transaction_costs": self.transaction_costs,
            "net_pnl": self.net_pnl,
            "starting_balance": self.starting_balance,
            "final_balance": self.final_balance,
            "final_equity": self.final_equity,
            "final_position_state": self.final_position_state,
            "last_timestamp": self.last_timestamp,
            "reconciliation_ok": self.reconciliation_ok,
            "reconciliation_reasons": list(self.reconciliation_reasons),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_paper_session_report(
    session: JournaledPaperSession, manifest: JournalManifest
) -> PaperSessionReport:
    entries = session.journal.entries
    ledger = session.ledger
    trades = tuple(ledger.engine.trade_records)
    rejected_reasons = Counter(
        reason
        for entry in entries
        if not entry.accepted
        for reason in entry.reasons
    )
    sessions = tuple(sorted({entry.session_date.isoformat() for entry in entries}))
    closed_sessions = tuple(sorted({
        entry.session_date.isoformat()
        for entry in entries
        if entry.operation == "session_close" and entry.accepted
    }))
    final_snapshot = ledger.snapshot()
    reconciliation_reasons: list[str] = []
    if not entries:
        reconciliation_reasons.append("no_journal_entries")
    if entries and entries[-1].snapshot != final_snapshot:
        reconciliation_reasons.append("journal_tail_snapshot_mismatch")
    expected_realized = sum(trade.net_pnl for trade in trades)
    expected_balance = ledger.starting_balance + expected_realized
    if not isclose(ledger.realized_pnl, expected_realized, rel_tol=0.0, abs_tol=1e-12):
        reconciliation_reasons.append("realized_pnl_mismatch")
    if not isclose(ledger.balance, expected_balance, rel_tol=0.0, abs_tol=1e-12):
        reconciliation_reasons.append("balance_mismatch")
    if ledger.engine.position is not None:
        reconciliation_reasons.append("open_position_unreconciled")
    if ledger.current_session_date is not None and ledger.closed_session_date != ledger.current_session_date:
        reconciliation_reasons.append("session_not_closed")
    last_timestamp = entries[-1].timestamp.isoformat() if entries else None
    return PaperSessionReport(
        manifest_hash=manifest.configuration_hash,
        journal_schema_version=manifest.journal_schema_version,
        entry_count=len(entries),
        event_count=sum(entry.operation == "event" for entry in entries),
        session_close_count=sum(entry.operation == "session_close" for entry in entries),
        accepted_operations=sum(entry.accepted for entry in entries),
        rejected_operations=sum(not entry.accepted for entry in entries),
        rejected_reason_counts=tuple(sorted(rejected_reasons.items())),
        sessions=sessions,
        closed_sessions=closed_sessions,
        trade_count=len(trades),
        gross_pnl=sum(trade.gross_pnl for trade in trades),
        transaction_costs=sum(trade.transaction_costs for trade in trades),
        net_pnl=sum(trade.net_pnl for trade in trades),
        starting_balance=ledger.starting_balance,
        final_balance=ledger.balance,
        final_equity=final_snapshot.equity,
        final_position_state=final_snapshot.position_state.value,
        last_timestamp=last_timestamp,
        reconciliation_ok=not reconciliation_reasons,
        reconciliation_reasons=tuple(reconciliation_reasons),
    )
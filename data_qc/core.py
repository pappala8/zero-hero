"""Provider-neutral, fail-closed QC for normalized market data."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from math import isfinite
from typing import Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from data import ContractIdentity, InstrumentKind, NormalizedBar, RawReleaseMetadata, TradingDayKind


class QCStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class QCConfig:
    expected_interval: timedelta
    mandatory_checks: tuple[str, ...]
    expected_trading_days: Mapping[date, TradingDayKind]
    strike_increment: Optional[float] = None
    authoritative_reference_available: bool = False
    expected_option_contracts: Optional[Mapping[date, frozenset[ContractIdentity]]] = None


@dataclass(frozen=True)
class QCCheckResult:
    name: str
    status: QCStatus
    blocking: bool
    checked_count: int
    affected: tuple[str, ...] = ()
    details: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status.value,
            "blocking": self.blocking,
            "checked_count": self.checked_count,
            "affected": list(self.affected),
            "details": list(self.details),
        }


@dataclass(frozen=True)
class QCReport:
    release_id: str
    normalization_version: str
    execution_timestamp: datetime
    status: QCStatus
    checks: tuple[QCCheckResult, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "release_id": self.release_id,
            "normalization_version": self.normalization_version,
            "execution_timestamp": self.execution_timestamp.isoformat(),
            "status": self.status.value,
            "checks": [check.as_dict() for check in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def human_report(self) -> str:
        lines = [f"release={self.release_id}", f"status={self.status.value}"]
        lines.extend(
            f"{check.name}: {check.status.value} ({'; '.join(check.details)})"
            for check in self.checks
        )
        return "\n".join(lines)


def _result(name: str, status: QCStatus, blocking: bool, count: int, affected=(), details=()) -> QCCheckResult:
    return QCCheckResult(name, status, blocking, count, tuple(sorted(str(item) for item in affected)), tuple(details))


def _identity_text(bar: NormalizedBar) -> str:
    return f"{bar.instrument.value}:{bar.contract.as_tuple()}@{bar.event_timestamp.isoformat()}"


def _check_schema(bars: list[NormalizedBar]) -> QCCheckResult:
    invalid = [bar.source_record_id for bar in bars if not bar.normalization_version or not bar.raw_release_id]
    return _result("schema", QCStatus.FAIL if invalid else QCStatus.PASS, True, len(bars), invalid, ("required normalized metadata missing",) if invalid else ())


def _check_timestamps(bars: list[NormalizedBar]) -> QCCheckResult:
    invalid = [bar.source_record_id for bar in bars if bar.event_timestamp.tzinfo is None or bar.event_timestamp.utcoffset() is None]
    return _result("timestamps_timezone", QCStatus.FAIL if invalid else QCStatus.PASS, True, len(bars), invalid, ("timestamp timezone missing",) if invalid else ())


def _check_sessions(bars: list[NormalizedBar], config: QCConfig) -> QCCheckResult:
    invalid = [
        bar.source_record_id
        for bar in bars
        if (
            bar.event_timestamp.astimezone(ZoneInfo(bar.session.timezone)).date() != bar.session.session_date
            or bar.event_timestamp.astimezone(ZoneInfo(bar.session.timezone)).time() <= bar.session.session_open
            or bar.event_timestamp.astimezone(ZoneInfo(bar.session.timezone)).time() > bar.session.session_close
            or (
                datetime.combine(bar.session.session_date, bar.event_timestamp.astimezone(ZoneInfo(bar.session.timezone)).time())
                - datetime.combine(bar.session.session_date, bar.session.session_open)
            ).total_seconds() % config.expected_interval.total_seconds() != 0
        )
    ]
    return _result("trading_sessions", QCStatus.FAIL if invalid else QCStatus.PASS, True, len(bars), invalid, ("record outside declared session",) if invalid else ())


def _check_duplicates(bars: list[NormalizedBar]) -> QCCheckResult:
    counts = Counter(bar.identity_key for bar in bars)
    duplicate_keys = [key for key, count in counts.items() if count > 1]
    affected = [str(key) for key in duplicate_keys]
    return _result("duplicate_rows", QCStatus.FAIL if affected else QCStatus.PASS, True, len(bars), affected, ("duplicate timestamp and contract identity",) if affected else ())


def _check_ohlc(bars: list[NormalizedBar]) -> QCCheckResult:
    invalid = []
    for bar in bars:
        values = (bar.open, bar.high, bar.low, bar.close)
        if any(value is None or not isfinite(value) or value < 0 for value in values):
            invalid.append(_identity_text(bar))
        elif bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
            invalid.append(_identity_text(bar))
    return _result("ohlc_price_validity", QCStatus.FAIL if invalid else QCStatus.PASS, True, len(bars), invalid, ("invalid or missing OHLC relationship",) if invalid else ())


def _check_volume_oi(bars: list[NormalizedBar]) -> QCCheckResult:
    invalid = []
    review = []
    for bar in bars:
        if bar.volume is not None and (not isfinite(bar.volume) or bar.volume < 0):
            invalid.append(_identity_text(bar))
        if bar.open_interest is not None and (not isfinite(bar.open_interest) or bar.open_interest < 0):
            invalid.append(_identity_text(bar))
        if bar.instrument is InstrumentKind.OPTION and (bar.volume is None or bar.open_interest is None):
            invalid.append(_identity_text(bar))
        if bar.instrument is InstrumentKind.SPOT and bar.volume is None:
            review.append(_identity_text(bar))
    if invalid:
        return _result("volume_open_interest", QCStatus.FAIL, True, len(bars), invalid, ("required or non-negative volume/OI invalid",))
    if review:
        return _result("volume_open_interest", QCStatus.REVIEW, False, len(bars), review, ("spot volume unavailable; use an explicit tradable proxy",))
    return _result("volume_open_interest", QCStatus.PASS, True, len(bars))


def _check_contracts(bars: list[NormalizedBar], config: QCConfig) -> QCCheckResult:
    invalid = []
    for bar in bars:
        contract = bar.contract
        if bar.instrument is InstrumentKind.OPTION:
            if contract.expiry is None or contract.strike is None or contract.right not in {"CE", "PE"}:
                invalid.append(_identity_text(bar))
            elif contract.expiry < bar.session.session_date or contract.strike <= 0:
                invalid.append(_identity_text(bar))
            elif config.strike_increment is not None and abs((contract.strike / config.strike_increment) - round(contract.strike / config.strike_increment)) > 1e-9:
                invalid.append(_identity_text(bar))
        elif contract.underlying != "NIFTY 50" or contract.expiry is not None or contract.strike is not None or contract.right is not None:
            invalid.append(_identity_text(bar))
    return _result("contract_identity_expiry_strike_right", QCStatus.FAIL if invalid else QCStatus.PASS, True, len(bars), invalid, ("contract identity, expiry, strike, or right invalid",) if invalid else ())


def _check_continuity(bars: list[NormalizedBar], config: QCConfig) -> QCCheckResult:
    """Apply strict continuity to spot/futures and non-blocking review to options.

    Listed option contracts are not assumed to trade continuously unless an explicit
    expected-contract universe is supplied. This prevents inactivity from being
    misclassified as provider corruption.
    """
    by_instrument_session: dict[object, list[NormalizedBar]] = defaultdict(list)
    for bar in bars:
        if bar.instrument is not InstrumentKind.OPTION:
            by_instrument_session[(bar.instrument, bar.session.session_date)].append(bar)
    gaps = []
    for key, records in by_instrument_session.items():
        records.sort(key=lambda bar: bar.event_timestamp.isoformat())
        first = records[0]
        zone = ZoneInfo(first.session.timezone)
        expected_first = datetime.combine(first.session.session_date, first.session.session_open, tzinfo=zone) + config.expected_interval
        expected_last = datetime.combine(first.session.session_date, first.session.session_close, tzinfo=zone)
        if records[0].event_timestamp != expected_first:
            gaps.append(f"{key}:expected_first={expected_first.isoformat()}")
        if records[-1].event_timestamp != expected_last:
            gaps.append(f"{key}:expected_last={expected_last.isoformat()}")
        for previous, current in zip(records, records[1:]):
            if (previous.event_timestamp.tzinfo is None or previous.event_timestamp.utcoffset() is None or
                    current.event_timestamp.tzinfo is None or current.event_timestamp.utcoffset() is None):
                continue
            if current.event_timestamp - previous.event_timestamp > config.expected_interval:
                gaps.append(f"{key}:{previous.event_timestamp.isoformat()}->{current.event_timestamp.isoformat()}")
    if gaps:
        return _result("missing_candles_continuity", QCStatus.FAIL, True, len(bars), gaps, ("unexpected underlying/futures continuity gap",))
    option_count = sum(1 for bar in bars if bar.instrument is InstrumentKind.OPTION)
    if option_count:
        return _result("missing_candles_continuity", QCStatus.REVIEW, False, len(bars), (), ("option contracts are not assumed continuously tradable; use expected-contract completeness for chain coverage",))
    return _result("missing_candles_continuity", QCStatus.PASS, True, len(bars))

def _check_trading_days(bars: list[NormalizedBar], config: QCConfig) -> QCCheckResult:
    observed = {bar.session.session_date for bar in bars}
    blocking = []
    review = []
    for session_date, kind in config.expected_trading_days.items():
        if session_date in observed:
            continue
        if kind in {TradingDayKind.EXPECTED, TradingDayKind.UNEXPLAINED_MISSING}:
            blocking.append(session_date.isoformat())
        elif kind is TradingDayKind.PROVIDER_OUTAGE:
            review.append(session_date.isoformat())
    if blocking:
        return _result("trading_day_completeness", QCStatus.FAIL, True, len(observed), blocking, ("unexplained expected trading session missing",))
    if review:
        return _result("trading_day_completeness", QCStatus.REVIEW, True, len(observed), review, ("provider outage requires disposition",))
    return _result("trading_day_completeness", QCStatus.PASS, True, len(observed))


def _check_option_chain(bars: list[NormalizedBar], config: QCConfig) -> QCCheckResult:
    option_bars = [bar for bar in bars if bar.instrument is InstrumentKind.OPTION]
    if not option_bars:
        return _result("option_chain_completeness", QCStatus.PASS, False, len(bars), details=("not applicable: no option records",))
    if config.expected_option_contracts is None:
        return _result("option_chain_completeness", QCStatus.REVIEW, True, len(option_bars), details=("expected option contract universe not supplied",))
    observed = defaultdict(set)
    for bar in option_bars:
        observed[bar.session.session_date].add(bar.contract)
    missing = []
    for session_date, expected in config.expected_option_contracts.items():
        missing.extend(f"{session_date.isoformat()}:{contract.as_tuple()}" for contract in expected - observed[session_date])
    return _result("option_chain_completeness", QCStatus.FAIL if missing else QCStatus.PASS, True, len(option_bars), missing, ("expected option contract missing",) if missing else ())


def _check_reference(bars: list[NormalizedBar], config: QCConfig) -> QCCheckResult:
    if config.authoritative_reference_available:
        return _result("reference_comparison", QCStatus.PASS, False, len(bars))
    return _result("reference_comparison", QCStatus.REVIEW, False, len(bars), details=("authoritative reference data unavailable",))


def _check_raw_integrity(release: RawReleaseMetadata) -> QCCheckResult:
    missing = []
    if not release.release_id:
        missing.append("release_id")
    if not release.source:
        missing.append("source")
    if not release.schema_version:
        missing.append("schema_version")
    if release.retrieval_timestamp.tzinfo is None or release.retrieval_timestamp.utcoffset() is None:
        missing.append("retrieval_timestamp_timezone")
    review = []
    if release.checksum is None:
        review.append("checksum")
    if release.file_size is None:
        review.append("file_size")
    if missing:
        return _result("raw_data_integrity", QCStatus.FAIL, True, 1, missing, ("raw release metadata missing",))
    if review:
        return _result("raw_data_integrity", QCStatus.REVIEW, False, 1, review, ("raw integrity field unavailable",))
    return _result("raw_data_integrity", QCStatus.PASS, True, 1)


def run_qc(
    records: Iterable[NormalizedBar],
    release: RawReleaseMetadata,
    config: QCConfig,
    execution_timestamp: datetime,
) -> QCReport:
    bars = list(records)
    checks = (
        _check_schema(bars),
        _check_timestamps(bars),
        _check_sessions(bars, config),
        _check_duplicates(bars),
        _check_ohlc(bars),
        _check_volume_oi(bars),
        _check_contracts(bars, config),
        _check_continuity(bars, config),
        _check_trading_days(bars, config),
        _check_option_chain(bars, config),
        _check_reference(bars, config),
        _check_raw_integrity(release),
    )
    checks_by_name = {check.name: check for check in checks}
    mandatory = [checks_by_name[name] for name in config.mandatory_checks if name in checks_by_name]
    if any(check.status is QCStatus.FAIL for check in mandatory) or any(check.status is QCStatus.REVIEW and check.blocking for check in mandatory):
        status = QCStatus.FAIL
    elif any(check.status is QCStatus.REVIEW for check in checks):
        status = QCStatus.REVIEW
    else:
        status = QCStatus.PASS
    return QCReport(
        release_id=release.release_id,
        normalization_version=bars[0].normalization_version if bars else "",
        execution_timestamp=execution_timestamp,
        status=status,
        checks=checks,
    )
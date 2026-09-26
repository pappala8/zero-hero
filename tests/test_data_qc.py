from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from data import (
    ContractIdentity,
    InstrumentKind,
    RawMarketRecord,
    RawReleaseMetadata,
    SessionInfo,
    TradingDayKind,
    normalize_records,
)
from data_qc import QCConfig, QCStatus, run_qc

IST = ZoneInfo("Asia/Kolkata")
SESSION_DATE = date(2026, 1, 5)


def release(*, complete_integrity: bool = True) -> RawReleaseMetadata:
    return RawReleaseMetadata(
        release_id="SYNTHETIC_RELEASE_V1",
        source="synthetic-fixture",
        retrieval_timestamp=datetime(2026, 1, 5, 8, 0, tzinfo=IST),
        schema_version="RAW_V1",
        checksum="abc123" if complete_integrity else None,
        file_size=100 if complete_integrity else None,
        partition_id="part-1",
        source_metadata={"fixture": "true"},
    )


def session(session_date: date = SESSION_DATE) -> SessionInfo:
    return SessionInfo(
        session_date=session_date,
        timezone="Asia/Kolkata",
        session_open=time(9, 15),
        session_close=time(9, 18),
        calendar_id="SYNTHETIC_CALENDAR_V1",
    )


def timestamp(minute: int, session_date: date = SESSION_DATE) -> datetime:
    return datetime.combine(session_date, time(9, minute), tzinfo=IST)


def spot_records(*, session_date: date = SESSION_DATE) -> list[RawMarketRecord]:
    values = ((16, 100, 101, 99, 100.5), (17, 100.5, 102, 100, 101.5), (18, 101.5, 103, 101, 102.5))
    return [
        RawMarketRecord(
            release=release(),
            source_record_id=f"spot-{minute}",
            instrument=InstrumentKind.SPOT,
            contract=ContractIdentity("NIFTY 50"),
            timestamp=timestamp(minute, session_date),
            session=session(session_date),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=100,
        )
        for minute, open_, high, low, close in values
    ]


def option_record(*, right: str | None = "CE", volume: float | None = 100, open_interest: float | None = 500) -> RawMarketRecord:
    return RawMarketRecord(
        release=release(),
        source_record_id="option-16",
        instrument=InstrumentKind.OPTION,
        contract=ContractIdentity("NIFTY 50", date(2026, 1, 8), 100, right),
        timestamp=timestamp(16),
        session=session(),
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=volume,
        open_interest=open_interest,
    )


def qc_config(**overrides) -> QCConfig:
    values = dict(
        expected_interval=timedelta(minutes=1),
        mandatory_checks=(
            "schema",
            "timestamps_timezone",
            "trading_sessions",
            "duplicate_rows",
            "ohlc_price_validity",
            "volume_open_interest",
            "contract_identity_expiry_strike_right",
            "missing_candles_continuity",
            "trading_day_completeness",
            "option_chain_completeness",
            "reference_comparison",
            "raw_data_integrity",
        ),
        expected_trading_days={SESSION_DATE: TradingDayKind.EXPECTED},
        authoritative_reference_available=True,
    )
    values.update(overrides)
    return QCConfig(**values)


def qc(records, *, metadata=None, config=None):
    normalized = normalize_records(records, "NORMALIZED_V1")
    return run_qc(
        normalized,
        metadata or release(),
        config or qc_config(),
        datetime(2026, 1, 5, 16, 0, tzinfo=IST),
    )


class DataQCTests(unittest.TestCase):
    def test_valid_normalized_spot_release_passes(self) -> None:
        report = qc(spot_records())

        self.assertEqual(report.status, QCStatus.PASS)
        self.assertEqual(report.release_id, "SYNTHETIC_RELEASE_V1")
        self.assertEqual(report.normalization_version, "NORMALIZED_V1")

    def test_normalization_preserves_invalid_raw_values_without_repair(self) -> None:
        invalid = replace(spot_records()[0], high=90)
        normalized = normalize_records([invalid], "NORMALIZED_V1")[0]

        self.assertEqual(normalized.high, 90)
        self.assertEqual(normalized.raw_release_id, "SYNTHETIC_RELEASE_V1")

    def test_invalid_ohlc_fails(self) -> None:
        invalid = replace(spot_records()[0], high=90)
        report = qc([invalid, *spot_records()[1:]])

        self.assertEqual(report.status, QCStatus.FAIL)
        self.assertEqual(next(check for check in report.checks if check.name == "ohlc_price_validity").status, QCStatus.FAIL)

    def test_missing_bar_fails_continuity(self) -> None:
        report = qc(spot_records()[:2])

        self.assertEqual(report.status, QCStatus.FAIL)
        self.assertEqual(next(check for check in report.checks if check.name == "missing_candles_continuity").status, QCStatus.FAIL)

    def test_duplicate_record_fails(self) -> None:
        records = spot_records()
        report = qc([*records, records[0]])

        self.assertEqual(report.status, QCStatus.FAIL)
        self.assertEqual(next(check for check in report.checks if check.name == "duplicate_rows").status, QCStatus.FAIL)

    def test_timestamp_and_session_error_fails(self) -> None:
        invalid_timestamp = replace(spot_records()[0], timestamp=datetime(2026, 1, 5, 9, 16))
        report = qc([invalid_timestamp, *spot_records()[1:]])

        self.assertEqual(report.status, QCStatus.FAIL)
        self.assertEqual(next(check for check in report.checks if check.name == "timestamps_timezone").status, QCStatus.FAIL)

    def test_contract_identity_error_fails(self) -> None:
        report = qc([option_record(right=None)])

        self.assertEqual(report.status, QCStatus.FAIL)
        self.assertEqual(next(check for check in report.checks if check.name == "contract_identity_expiry_strike_right").status, QCStatus.FAIL)

    def test_volume_and_open_interest_error_fails(self) -> None:
        report = qc([option_record(volume=-1, open_interest=None)])

        self.assertEqual(report.status, QCStatus.FAIL)
        self.assertEqual(next(check for check in report.checks if check.name == "volume_open_interest").status, QCStatus.FAIL)

    def test_unresolved_integrity_reference_and_spot_volume_are_review(self) -> None:
        report = qc(
            [replace(record, volume=None) for record in spot_records()],
            metadata=release(complete_integrity=False),
            config=qc_config(authoritative_reference_available=False),
        )

        self.assertEqual(report.status, QCStatus.REVIEW)
        review_names = {check.name for check in report.checks if check.status is QCStatus.REVIEW}
        self.assertEqual(review_names, {"volume_open_interest", "reference_comparison", "raw_data_integrity"})

    def test_missing_expected_trading_day_fails(self) -> None:
        next_day = date(2026, 1, 6)
        report = qc(
            spot_records(),
            config=qc_config(expected_trading_days={SESSION_DATE: TradingDayKind.EXPECTED, next_day: TradingDayKind.EXPECTED}),
        )

        self.assertEqual(report.status, QCStatus.FAIL)
        self.assertEqual(next(check for check in report.checks if check.name == "trading_day_completeness").status, QCStatus.FAIL)


if __name__ == "__main__":
    unittest.main()
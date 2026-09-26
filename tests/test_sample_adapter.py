from __future__ import annotations

import tempfile
import unittest
import pytest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

pytest.importorskip("pyarrow", reason="pyarrow is required for sample parquet adapter tests")
import pyarrow as pa
import pyarrow.parquet as pq

from data import InstrumentKind
from data.providers.optionsdata_shop import ingest_sample_directory

IST = ZoneInfo("Asia/Kolkata")


def native_row(
    *,
    timestamp="2026-09-15 09:15:00",
    right="Call",
    high=101.0,
    volume=10,
    open_interest=100,
):
    return {
        "datetime": timestamp,
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
        "product_type": "Options",
        "expiry_date": "22-SEP-2026",
        "strike_price": 25000.0,
        "right": right,
        "open": 100.0,
        "high": high,
        "low": 99.0,
        "close": 100.5,
        "volume": volume,
        "open_interest": open_interest,
    }


def write_fixture(directory: str, rows: list[dict]) -> Path:
    path = Path(directory) / "NIFTY_OPT_1min_2026-09-15_exp2026-09-22.parquet"
    columns = {key: [row[key] for row in rows] for key in rows[0]}
    columns["datetime"] = pa.array(
        [datetime.fromisoformat(value) if isinstance(value, str) else value for value in columns["datetime"]],
        type=pa.timestamp("us"),
    )
    table = pa.table(columns)
    pq.write_table(table, path)
    return path


class SampleAdapterTests(unittest.TestCase):
    def test_native_fields_map_to_contract_and_causal_event_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [native_row()])
            result = ingest_sample_directory(
                directory,
                retrieval_timestamp=datetime(2026, 9, 26, tzinfo=IST),
            )

            self.assertEqual(result.issues, ())
            self.assertEqual(len(result.raw_records), 1)
            self.assertEqual(len(result.normalized_records), 1)
            normalized = result.normalized_records[0]
            self.assertEqual(normalized.instrument, InstrumentKind.OPTION)
            self.assertEqual(normalized.contract.underlying, "NIFTY 50")
            self.assertEqual(normalized.contract.right, "CE")
            self.assertEqual(normalized.event_timestamp.hour, 9)
            self.assertEqual(normalized.event_timestamp.minute, 16)
            self.assertEqual(result.raw_records[0].timestamp.minute, 15)

    def test_provider_right_mapping_supports_put(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [native_row(right="Put")])
            result = ingest_sample_directory(directory, retrieval_timestamp=datetime(2026, 9, 26, tzinfo=IST))

            self.assertEqual(result.normalized_records[0].contract.right, "PE")

    def test_malformed_timestamp_is_rejected_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "NIFTY_OPT_1min_2026-09-15_exp2026-09-22.parquet"
            row = native_row()
            columns = {key: [value] for key, value in row.items()}
            columns["datetime"] = pa.array(["not-a-timestamp"], type=pa.string())
            pq.write_table(pa.table(columns), path)

            result = ingest_sample_directory(directory, retrieval_timestamp=datetime(2026, 9, 26, tzinfo=IST))

            self.assertEqual(len(result.raw_records), 0)
            self.assertEqual(len(result.issues), 1)
            self.assertIn("timestamp", result.issues[0].message)

    def test_duplicate_contract_rows_are_preserved_for_qc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [native_row(), native_row()])
            result = ingest_sample_directory(directory, retrieval_timestamp=datetime(2026, 9, 26, tzinfo=IST))

            self.assertEqual(len(result.issues), 0)
            self.assertEqual(len(result.normalized_records), 2)
            self.assertEqual(
                result.normalized_records[0].identity_key,
                result.normalized_records[1].identity_key,
            )

    def test_invalid_ohlc_and_volume_values_are_preserved_for_qc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [native_row(high=90.0, volume=-1, open_interest=-2)])
            result = ingest_sample_directory(directory, retrieval_timestamp=datetime(2026, 9, 26, tzinfo=IST))

            self.assertEqual(len(result.issues), 0)
            self.assertEqual(result.normalized_records[0].high, 90.0)
            self.assertEqual(result.normalized_records[0].volume, -1.0)
            self.assertEqual(result.normalized_records[0].open_interest, -2.0)


if __name__ == "__main__":
    unittest.main()
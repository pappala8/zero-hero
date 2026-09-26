from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from data import normalize_records
from data.release import NormalizedReleaseStore, ReleaseError

from test_data_qc import qc, release, spot_records


def valid_inputs():
    raw = release()
    records = spot_records()
    normalized = normalize_records(records, "NORMALIZED_V1")
    report = qc(records)
    return raw, normalized, report


class DataReleaseTests(unittest.TestCase):
    def test_valid_release_binds_qc_and_writes_deterministic_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw, normalized, report = valid_inputs()
            store = NormalizedReleaseStore(directory)
            manifest = store.publish(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="V1",
                records=normalized,
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )

            validated = store.validate("NIFTY_SYNTHETIC", "V1", raw, report)

            self.assertEqual(validated, manifest)
            self.assertEqual(manifest.qc_status.value, "PASS")
            self.assertEqual(manifest.total_rows, 3)
            self.assertEqual(manifest.trading_dates, (normalized[0].session.session_date,))
            self.assertEqual(len(manifest.partitions), 1)
            self.assertTrue((Path(directory) / "NIFTY_SYNTHETIC" / "V1" / "manifest.json").exists())

    def test_publish_consumes_generator_without_materializing_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw, normalized, report = valid_inputs()
            consumed = []
            def source():
                for record in normalized:
                    consumed.append(record.source_record_id)
                    yield record
            manifest = NormalizedReleaseStore(directory).publish(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="STREAM",
                records=source(),
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )
            self.assertEqual(manifest.total_rows, len(normalized))
            self.assertEqual(len(consumed), len(normalized))

    def test_staged_chunks_merge_all_rows_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw, normalized, report = valid_inputs()
            store = NormalizedReleaseStore(directory)
            staging = Path(directory) / "staging"
            first = store.stage_partition(records=normalized[:1], staging_directory=staging, chunk_name="first.jsonl")
            second = store.stage_partition(records=normalized[1:], staging_directory=staging, chunk_name="second.jsonl")

            manifest = store.publish_staged(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="STAGED",
                staged_partitions=(first, second),
                staging_directory=staging,
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )

            self.assertEqual(manifest.total_rows, len(normalized))
            self.assertEqual(manifest.partitions[0].row_count, len(normalized))
            validated = store.validate("NIFTY_SYNTHETIC", "STAGED", raw, report)
            self.assertEqual(validated, manifest)

    def test_qc_fail_is_not_releasable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw = release()
            records = spot_records()
            invalid = [replace(records[0], high=90), *records[1:]]
            normalized = normalize_records(invalid, "NORMALIZED_V1")
            report = qc(invalid)

            with self.assertRaises(ReleaseError):
                NormalizedReleaseStore(directory).publish(
                    dataset_id="NIFTY_SYNTHETIC",
                    dataset_version="FAIL",
                    records=normalized,
                    raw_release=raw,
                    qc_report=report,
                    normalization_version="NORMALIZED_V1",
                    execution_timestamp=report.execution_timestamp,
                )

    def test_blocking_review_is_not_releasable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw = release()
            records = spot_records()
            normalized = normalize_records(records, "NORMALIZED_V1")
            report = qc(records, config=None)
            # The synthetic spot-only report has no blocking review; use a report
            # with an option record and no expected chain universe instead.
            option = __import__("test_data_qc").option_record()
            review_report = qc([option], config=None)

            self.assertTrue(any(check.blocking and check.status.value == "REVIEW" for check in review_report.checks))
            with self.assertRaises(ReleaseError):
                NormalizedReleaseStore(directory).publish(
                    dataset_id="NIFTY_SYNTHETIC",
                    dataset_version="REVIEW",
                    records=normalized,
                    raw_release=raw,
                    qc_report=review_report,
                    normalization_version="NORMALIZED_V1",
                    execution_timestamp=report.execution_timestamp,
                )

    def test_repeated_identical_release_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw, normalized, report = valid_inputs()
            store = NormalizedReleaseStore(directory)
            first = store.publish(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="V1",
                records=normalized,
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )
            manifest_path = Path(directory) / "NIFTY_SYNTHETIC" / "V1" / "manifest.json"
            before = manifest_path.read_bytes()
            second = store.publish(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="V1",
                records=tuple(reversed(normalized)),
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )

            self.assertEqual(first, second)
            self.assertEqual(before, manifest_path.read_bytes())

    def test_manifest_mismatch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw, normalized, report = valid_inputs()
            store = NormalizedReleaseStore(directory)
            store.publish(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="V1",
                records=normalized,
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )
            changed_report = replace(report, execution_timestamp=report.execution_timestamp.replace(minute=1))

            with self.assertRaises(ReleaseError):
                store.validate("NIFTY_SYNTHETIC", "V1", raw, changed_report)

    def test_tampered_partition_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw, normalized, report = valid_inputs()
            store = NormalizedReleaseStore(directory)
            manifest = store.publish(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="V1",
                records=normalized,
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )
            path = Path(directory) / "NIFTY_SYNTHETIC" / "V1" / manifest.partitions[0].relative_path
            path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

            with self.assertRaises(ReleaseError):
                store.validate("NIFTY_SYNTHETIC", "V1", raw, report)

    def test_missing_partition_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw, normalized, report = valid_inputs()
            store = NormalizedReleaseStore(directory)
            manifest = store.publish(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="V1",
                records=normalized,
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )
            path = Path(directory) / "NIFTY_SYNTHETIC" / "V1" / manifest.partitions[0].relative_path
            path.unlink()

            with self.assertRaises(ReleaseError):
                store.validate("NIFTY_SYNTHETIC", "V1", raw, report)

    def test_raw_metadata_mismatch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw, normalized, report = valid_inputs()
            store = NormalizedReleaseStore(directory)
            store.publish(
                dataset_id="NIFTY_SYNTHETIC",
                dataset_version="V1",
                records=normalized,
                raw_release=raw,
                qc_report=report,
                normalization_version="NORMALIZED_V1",
                execution_timestamp=report.execution_timestamp,
            )
            changed_raw = replace(raw, release_id="OTHER_RELEASE")

            with self.assertRaises(ReleaseError):
                store.validate("NIFTY_SYNTHETIC", "V1", changed_raw, report)


if __name__ == "__main__":
    unittest.main()
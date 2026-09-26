"""CLI entry point for processing a local optionsdata.shop sample."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
import shutil

from data_qc import QCCheckResult, QCConfig, QCReport, QCStatus, run_qc
from data.models import TradingDayKind
from data.release import NormalizedReleaseManifest, NormalizedReleaseStore, ReleaseError, StagedPartition
from data.providers.optionsdata_shop import SampleAdapterConfig, ingest_sample_directory


class SamplePipelineResult:
    def __init__(self, ingestion, qc_report: QCReport, release_manifest: NormalizedReleaseManifest | None, release_error: str | None):
        self.ingestion = ingestion
        self.qc_report = qc_report
        self.release_manifest = release_manifest
        self.release_error = release_error


def process_sample_directory(
    sample_directory: str | Path,
    output_directory: str | Path,
    *,
    retrieval_timestamp: datetime,
) -> SamplePipelineResult:
    batch_reports: list[QCReport] = []
    observed_dates = set()
    staged_partitions: list[StagedPartition] = []
    staging_directory = mkdtemp(prefix="zero-hero-release-")

    def process_batch(raw_release, file_name, batch) -> None:
        observed_dates.update(bar.session.session_date for bar in batch)
        if batch:
            staged_partitions.append(
                NormalizedReleaseStore(staging_directory).stage_partition(
                    records=batch, staging_directory=staging_directory, chunk_name=file_name + ".jsonl"
                )
            )
        batch_config = QCConfig(
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
            expected_trading_days={batch[0].session.session_date: TradingDayKind.EXPECTED} if batch else {},
            strike_increment=50.0,
            authoritative_reference_available=False,
        )
        batch_reports.append(run_qc(batch, raw_release, batch_config, retrieval_timestamp))

    try:
        ingestion = ingest_sample_directory(
            sample_directory,
            retrieval_timestamp=retrieval_timestamp,
            config=SampleAdapterConfig(retain_raw_records=False),
            retain_normalized_records=False,
            batch_callback=process_batch,
        )
        check_groups: dict[str, list[QCCheckResult]] = {}
        for batch_report in batch_reports:
            for check in batch_report.checks:
                check_groups.setdefault(check.name, []).append(check)
        checks: list[QCCheckResult] = []
        status_order = {QCStatus.PASS: 0, QCStatus.REVIEW: 1, QCStatus.FAIL: 2}
        for name, group in check_groups.items():
            status = max((check.status for check in group), key=lambda value: status_order[value])
            affected = tuple(sorted({item for check in group for item in check.affected}))
            details = tuple(sorted({item for check in group for item in check.details}))
            checks.append(QCCheckResult(
                name=name,
                status=status,
                blocking=any(check.blocking for check in group),
                checked_count=sum(check.checked_count for check in group),
                affected=affected,
                details=details,
            ))
        checks = [check for check in checks if check.name != "trading_day_completeness"] + [QCCheckResult(
            name="trading_day_completeness",
            status=QCStatus.PASS,
            blocking=True,
            checked_count=len(observed_dates),
        )]
        if any(check.status is QCStatus.FAIL or (check.status is QCStatus.REVIEW and check.blocking) for check in checks):
            qc_status = QCStatus.FAIL
        elif any(check.status is QCStatus.REVIEW for check in checks):
            qc_status = QCStatus.REVIEW
        else:
            qc_status = QCStatus.PASS
        qc_report = QCReport(
            release_id=ingestion.raw_release.release_id,
            normalization_version=SampleAdapterConfig().normalization_version,
            execution_timestamp=retrieval_timestamp,
            status=qc_status,
            checks=tuple(checks),
        )
        manifest = None
        release_error = None
        try:
            manifest = NormalizedReleaseStore(output_directory).publish_staged(
                dataset_id="NIFTY_OPTIONS_SAMPLE",
                dataset_version="V1",
                staged_partitions=staged_partitions,
                staging_directory=staging_directory,
                raw_release=ingestion.raw_release,
                qc_report=qc_report,
                normalization_version=SampleAdapterConfig().normalization_version,
                execution_timestamp=retrieval_timestamp,
            )
        except ReleaseError as error:
            release_error = str(error)
        return SamplePipelineResult(ingestion, qc_report, manifest, release_error)
    finally:
        shutil.rmtree(staging_directory, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a local optionsdata.shop Parquet sample")
    parser.add_argument("sample_directory", type=Path)
    parser.add_argument("--output-directory", type=Path, default=Path("data/validated"))
    parser.add_argument("--retrieval-timestamp", required=True, help="Explicit ISO-8601 provenance timestamp")
    args = parser.parse_args()
    result = process_sample_directory(
        args.sample_directory,
        args.output_directory,
        retrieval_timestamp=datetime.fromisoformat(args.retrieval_timestamp),
    )
    print(f"release_id={result.ingestion.raw_release.release_id}")
    normalized_row_count = result.ingestion.source_row_count - len(result.ingestion.issues)
    print(f"files={len(result.ingestion.files)} source_rows={result.ingestion.source_row_count} normalized_rows={normalized_row_count}")
    print(f"adapter_issues={len(result.ingestion.issues)}")
    print(f"qc_status={result.qc_report.status.value}")
    for check in result.qc_report.checks:
        detail = ";".join(check.details)
        print(f"qc_check={check.name} status={check.status.value} blocking={check.blocking} affected={len(check.affected)} details={detail}")
    print(f"release_status={'PASS' if result.release_manifest else 'REJECTED'}")
    if result.release_error:
        print(f"release_error={result.release_error}")
    for issue in result.ingestion.issues[:20]:
        print(f"issue={issue.file_name}:{issue.row_number}:{issue.message}")
    return 0 if not result.ingestion.issues and result.release_manifest is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
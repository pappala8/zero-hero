"""Deterministic normalized dataset-release manifests and partition storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from data.models import InstrumentKind, NormalizedBar, RawReleaseMetadata
from data_qc import QCReport, QCStatus


RELEASE_SCHEMA_VERSION = "ZH_NORMALIZED_RELEASE_V1"


class ReleaseError(RuntimeError):
    pass


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in sorted(value, key=lambda item: str(item))]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _bar_dict(bar: NormalizedBar) -> dict[str, Any]:
    return {
        "normalization_version": bar.normalization_version,
        "raw_release_id": bar.raw_release_id,
        "source_record_id": bar.source_record_id,
        "instrument": bar.instrument.value,
        "contract": {
            "underlying": bar.contract.underlying,
            "expiry": bar.contract.expiry.isoformat() if bar.contract.expiry else None,
            "strike": bar.contract.strike,
            "right": bar.contract.right,
        },
        "event_timestamp": bar.event_timestamp.isoformat(),
        "session": {
            "session_date": bar.session.session_date.isoformat(),
            "timezone": bar.session.timezone,
            "session_open": bar.session.session_open.isoformat(),
            "session_close": bar.session.session_close.isoformat(),
            "calendar_id": bar.session.calendar_id,
            "day_kind": bar.session.day_kind.value,
            "is_special_session": bar.session.is_special_session,
        },
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "open_interest": bar.open_interest,
    }


def _bar_sort_key(bar: NormalizedBar) -> tuple[str, ...]:
    contract = bar.contract
    return (
        bar.event_timestamp.isoformat(),
        bar.instrument.value,
        contract.underlying,
        contract.expiry.isoformat() if contract.expiry else "",
        str(contract.strike) if contract.strike is not None else "",
        contract.right or "",
        bar.source_record_id,
    )


def _json_bar_sort_key(value: Mapping[str, Any]) -> tuple[str, ...]:
    contract = value["contract"]
    return (
        value["event_timestamp"],
        value["instrument"],
        contract["underlying"],
        contract["expiry"] or "",
        str(contract["strike"]) if contract["strike"] is not None else "",
        contract["right"] or "",
        value["source_record_id"],
    )


@dataclass(frozen=True)
class PartitionManifest:
    partition_id: str
    relative_path: str
    session_date: date
    instrument: InstrumentKind
    row_count: int
    content_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "partition_id": self.partition_id,
            "relative_path": self.relative_path,
            "session_date": self.session_date.isoformat(),
            "instrument": self.instrument.value,
            "row_count": self.row_count,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class NormalizedReleaseManifest:
    release_schema_version: str
    dataset_id: str
    dataset_version: str
    raw_release_id: str
    raw_source: str
    raw_checksum: str | None
    raw_schema_version: str
    normalization_version: str
    qc_release_id: str
    qc_status: QCStatus
    qc_report_hash: str
    trading_dates: tuple[date, ...]
    instruments: tuple[InstrumentKind, ...]
    contract_scope: tuple[str, ...]
    partitions: tuple[PartitionManifest, ...]
    total_rows: int
    content_hash: str
    manifest_hash: str

    def _without_hash(self) -> dict[str, Any]:
        return {
            "release_schema_version": self.release_schema_version,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "raw_release_id": self.raw_release_id,
            "raw_source": self.raw_source,
            "raw_checksum": self.raw_checksum,
            "raw_schema_version": self.raw_schema_version,
            "normalization_version": self.normalization_version,
            "qc_release_id": self.qc_release_id,
            "qc_status": self.qc_status.value,
            "qc_report_hash": self.qc_report_hash,
            "trading_dates": self.trading_dates,
            "instruments": self.instruments,
            "contract_scope": self.contract_scope,
            "partitions": tuple(partition.as_dict() for partition in self.partitions),
            "total_rows": self.total_rows,
            "content_hash": self.content_hash,
        }

    def as_dict(self) -> dict[str, Any]:
        value = self._without_hash()
        value["manifest_hash"] = self.manifest_hash
        return _json_value(value)

    def to_json(self) -> str:
        return _canonical(self.as_dict())


@dataclass(frozen=True)
class StagedPartition:
    """Immutable metadata for a normalized partition staged on disk."""

    partition_id: str
    relative_path: str
    session_date: date
    instrument: InstrumentKind
    row_count: int
    content_hash: str
    contract_scope: tuple[str, ...]


class NormalizedReleaseStore:
    """Write and validate one immutable normalized release under a local root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def publish(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        records: Iterable[NormalizedBar],
        raw_release: RawReleaseMetadata,
        qc_report: QCReport,
        normalization_version: str,
        execution_timestamp: datetime,
    ) -> NormalizedReleaseManifest:
        """Publish a normalized release through bounded staging and external merge.

        The input iterable is never materialized as one in-memory collection.
        Batches are bounded, written as deterministic staged partitions, and the
        final release is assembled by ``publish_staged``.
        """
        self._validate_release_metadata(raw_release, qc_report, normalization_version)
        batch_size = 50_000
        batch: list[NormalizedBar] = []
        saw_record = False
        with tempfile.TemporaryDirectory(prefix="zero-hero-release-") as staging_dir:
            staged: list[StagedPartition] = []
            chunk_counter = 0

            def flush() -> None:
                nonlocal chunk_counter, batch
                if not batch:
                    return
                for bar in batch:
                    if bar.normalization_version != normalization_version:
                        raise ReleaseError("normalization version mismatch")
                    if bar.raw_release_id != raw_release.release_id:
                        raise ReleaseError("normalized record raw release mismatch")
                grouped: dict[tuple[date, InstrumentKind], list[NormalizedBar]] = {}
                for bar in batch:
                    grouped.setdefault((bar.session.session_date, bar.instrument), []).append(bar)
                for key in sorted(grouped, key=lambda item: (item[0], item[1].value)):
                    chunk_counter += 1
                    staged.append(
                        self.stage_partition(
                            records=grouped[key],
                            staging_directory=staging_dir,
                            chunk_name=f"chunk-{chunk_counter:08d}.jsonl",
                        )
                    )
                batch = []

            for bar in records:
                saw_record = True
                batch.append(bar)
                if len(batch) >= batch_size:
                    flush()
            flush()
            if not saw_record:
                raise ReleaseError("cannot release an empty normalized dataset")
            return self.publish_staged(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                staged_partitions=staged,
                staging_directory=staging_dir,
                raw_release=raw_release,
                qc_report=qc_report,
                normalization_version=normalization_version,
                execution_timestamp=execution_timestamp,
            )

    def stage_partition(
        self,
        *,
        records: Iterable[NormalizedBar],
        staging_directory: str | Path,
        chunk_name: str | None = None,
    ) -> StagedPartition:
        """Write one deterministic partition to disk without retaining the whole release."""
        bars = tuple(records)
        if not bars:
            raise ReleaseError("cannot stage an empty normalized partition")
        session_dates = {bar.session.session_date for bar in bars}
        instruments = {bar.instrument for bar in bars}
        if len(session_dates) != 1 or len(instruments) != 1:
            raise ReleaseError("a staged partition must contain one session date and instrument")
        session_date = next(iter(session_dates))
        instrument = next(iter(instruments))
        ordered = tuple(sorted(bars, key=_bar_sort_key))
        partition_id = f"session={session_date.isoformat()}/instrument={instrument.value}"
        chunk_file = chunk_name or f"chunk-{ordered[0].source_record_id.replace(':', '_')}.jsonl"
        relative_path = f"{partition_id}/staged/{chunk_file}"
        root = Path(staging_directory)
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256()
        contract_scope: set[str] = set()
        row_count = 0
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for bar in ordered:
                line = _canonical(_bar_dict(bar)) + "\n"
                handle.write(line)
                content_hash.update(line.encode("utf-8"))
                contract_scope.add(_canonical(bar.contract.as_tuple()))
                row_count += 1
        temporary.replace(path)
        return StagedPartition(
            partition_id=partition_id,
            relative_path=relative_path,
            session_date=session_date,
            instrument=instrument,
            row_count=row_count,
            content_hash=content_hash.hexdigest(),
            contract_scope=tuple(sorted(contract_scope)),
        )

    def publish_staged(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        staged_partitions: Iterable[StagedPartition],
        staging_directory: str | Path,
        raw_release: RawReleaseMetadata,
        qc_report: QCReport,
        normalization_version: str,
        execution_timestamp: datetime,
    ) -> NormalizedReleaseManifest:
        """Publish all staged chunks with an external merge, without aggregate object retention."""
        chunks = tuple(staged_partitions)
        if not chunks:
            raise ReleaseError("cannot release an empty normalized dataset")
        self._validate_release_metadata(raw_release, qc_report, normalization_version)
        staging_root = Path(staging_directory)
        grouped: dict[str, list[StagedPartition]] = {}
        for chunk in chunks:
            grouped.setdefault(chunk.partition_id, []).append(chunk)
        if any(len(group) == 0 for group in grouped.values()):
            raise ReleaseError("empty staged partition group")

        final_partitions: list[PartitionManifest] = []
        trading_dates = tuple(sorted({chunk.session_date for chunk in chunks}))
        instruments = tuple(sorted({chunk.instrument for chunk in chunks}, key=lambda value: value.value))
        contract_scope = tuple(sorted({item for chunk in chunks for item in chunk.contract_scope}))
        release_root = self.root / dataset_id / dataset_version
        release_root.mkdir(parents=True, exist_ok=True)

        import heapq

        for partition_id in sorted(grouped):
            group = sorted(grouped[partition_id], key=lambda item: item.relative_path)
            session_date = group[0].session_date
            instrument = group[0].instrument
            if any(item.session_date != session_date or item.instrument != instrument for item in group):
                raise ReleaseError(f"inconsistent staged partition metadata: {partition_id}")
            handles = []
            heap: list[tuple[tuple[str, ...], int, str]] = []
            try:
                for index, chunk in enumerate(group):
                    source = staging_root / chunk.relative_path
                    if not source.exists():
                        raise ReleaseError(f"staged partition is missing: {chunk.partition_id}")
                    handle = source.open("r", encoding="utf-8")
                    handles.append(handle)
                    line = handle.readline()
                    if line:
                        value = json.loads(line)
                        heapq.heappush(heap, (_json_bar_sort_key(value), index, line))
                relative_path = f"{partition_id}.jsonl"
                destination = release_root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                content_hash = hashlib.sha256()
                row_count = 0
                with temporary.open("w", encoding="utf-8", newline="\n") as output:
                    while heap:
                        _, index, line = heapq.heappop(heap)
                        output.write(line)
                        content_hash.update(line.encode("utf-8"))
                        row_count += 1
                        next_line = handles[index].readline()
                        if next_line:
                            value = json.loads(next_line)
                            heapq.heappush(heap, (_json_bar_sort_key(value), index, next_line))
                actual_hash = content_hash.hexdigest()
                final_partition = PartitionManifest(
                    partition_id=partition_id,
                    relative_path=relative_path,
                    session_date=session_date,
                    instrument=instrument,
                    row_count=row_count,
                    content_hash=actual_hash,
                )
                if destination.exists():
                    if destination.read_bytes() != temporary.read_bytes():
                        raise ReleaseError("existing partition content does not match deterministic release")
                    temporary.unlink()
                else:
                    temporary.replace(destination)
                final_partitions.append(final_partition)
            finally:
                for handle in handles:
                    handle.close()

        partitions = tuple(sorted(final_partitions, key=lambda item: item.partition_id))
        aggregate = hashlib.sha256(
            "".join(f"{partition.partition_id}:{partition.content_hash}\n" for partition in partitions).encode("utf-8")
        ).hexdigest()
        qc_hash = hashlib.sha256(qc_report.to_json().encode("utf-8")).hexdigest()
        manifest_without_hash = {
            "release_schema_version": RELEASE_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "raw_release_id": raw_release.release_id,
            "raw_source": raw_release.source,
            "raw_checksum": raw_release.checksum,
            "raw_schema_version": raw_release.schema_version,
            "normalization_version": normalization_version,
            "qc_release_id": qc_report.release_id,
            "qc_status": qc_report.status,
            "qc_report_hash": qc_hash,
            "trading_dates": trading_dates,
            "instruments": instruments,
            "contract_scope": contract_scope,
            "partitions": partitions,
            "total_rows": sum(partition.row_count for partition in partitions),
            "content_hash": aggregate,
        }
        manifest_hash = hashlib.sha256(_canonical(manifest_without_hash).encode("utf-8")).hexdigest()
        manifest = NormalizedReleaseManifest(manifest_hash=manifest_hash, **manifest_without_hash)
        manifest_path = release_root / "manifest.json"
        qc_path = release_root / "qc_report.json"
        qc_content = qc_report.to_json() + "\n"
        manifest_content = manifest.to_json() + "\n"
        if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest_content:
            raise ReleaseError("existing release manifest does not match deterministic release")
        if not manifest_path.exists():
            manifest_path.write_text(manifest_content, encoding="utf-8", newline="\n")
        elif manifest_path.read_text(encoding="utf-8") != manifest_content:
            raise ReleaseError("existing release manifest does not match deterministic release")
        if not qc_path.exists():
            qc_path.write_text(qc_content, encoding="utf-8", newline="\n")
        elif qc_path.read_text(encoding="utf-8") != qc_content:
            raise ReleaseError("existing QC report does not match deterministic release")
        return self.validate(dataset_id, dataset_version, raw_release, qc_report)

    def validate(
        self,
        dataset_id: str,
        dataset_version: str,
        raw_release: RawReleaseMetadata,
        qc_report: QCReport,
    ) -> NormalizedReleaseManifest:
        release_root = self.root / dataset_id / dataset_version
        manifest_path = release_root / "manifest.json"
        qc_path = release_root / "qc_report.json"
        if not manifest_path.exists():
            raise ReleaseError("release manifest is missing")
        if not qc_path.exists():
            raise ReleaseError("QC report artifact is missing")
        try:
            if qc_path.read_text(encoding="utf-8") != qc_report.to_json() + "\n":
                raise ReleaseError("QC report artifact mismatch")
        except OSError as error:
            raise ReleaseError(f"QC report artifact unreadable: {error}") from error
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ReleaseError(f"release manifest is unreadable: {error}") from error
        manifest = self._manifest_from_dict(value)
        if manifest.dataset_id != dataset_id or manifest.dataset_version != dataset_version:
            raise ReleaseError("release manifest identity mismatch")
        if manifest.manifest_hash != hashlib.sha256(_canonical(manifest._without_hash()).encode("utf-8")).hexdigest():
            raise ReleaseError("release manifest hash mismatch")
        if manifest.raw_release_id != raw_release.release_id or manifest.raw_checksum != raw_release.checksum:
            raise ReleaseError("raw release metadata mismatch")
        qc_hash = hashlib.sha256(qc_report.to_json().encode("utf-8")).hexdigest()
        if manifest.qc_release_id != qc_report.release_id or manifest.qc_report_hash != qc_hash or manifest.qc_status is not qc_report.status:
            raise ReleaseError("QC result mismatch")
        if qc_report.status is QCStatus.FAIL or any(check.status is QCStatus.REVIEW and check.blocking for check in qc_report.checks):
            raise ReleaseError("QC result is not releasable")
        partition_hashes = []
        row_total = 0
        for partition in manifest.partitions:
            path = release_root / partition.relative_path
            if not path.exists():
                raise ReleaseError(f"partition is missing: {partition.partition_id}")
            content = path.read_text(encoding="utf-8")
            actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if actual_hash != partition.content_hash:
                raise ReleaseError(f"partition hash mismatch: {partition.partition_id}")
            rows = [line for line in content.splitlines() if line]
            if len(rows) != partition.row_count:
                raise ReleaseError(f"partition row count mismatch: {partition.partition_id}")
            row_total += len(rows)
            partition_hashes.append(f"{partition.partition_id}:{partition.content_hash}\n")
        aggregate = hashlib.sha256("".join(partition_hashes).encode("utf-8")).hexdigest()
        if row_total != manifest.total_rows or aggregate != manifest.content_hash:
            raise ReleaseError("release content summary mismatch")
        return manifest

    @staticmethod
    def _validate_release_metadata(raw_release: RawReleaseMetadata, qc_report: QCReport, normalization_version: str) -> None:
        if not normalization_version:
            raise ReleaseError("normalization_version is required")
        if qc_report.status is QCStatus.FAIL or any(check.status is QCStatus.REVIEW and check.blocking for check in qc_report.checks):
            raise ReleaseError("QC result is not releasable")

    @staticmethod
    def _validate_release_inputs(
        bars: tuple[NormalizedBar, ...], raw_release: RawReleaseMetadata, qc_report: QCReport, normalization_version: str
    ) -> None:
        if not bars:
            raise ReleaseError("cannot release an empty normalized dataset")
        NormalizedReleaseStore._validate_release_metadata(raw_release, qc_report, normalization_version)
        for bar in bars:
            if bar.normalization_version != normalization_version:
                raise ReleaseError("normalization version mismatch")
            if bar.raw_release_id != raw_release.release_id:
                raise ReleaseError("normalized record raw release mismatch")

    @staticmethod
    def _manifest_from_dict(value: Mapping[str, Any]) -> NormalizedReleaseManifest:
        try:
            partitions = tuple(
                PartitionManifest(
                    partition_id=item["partition_id"],
                    relative_path=item["relative_path"],
                    session_date=date.fromisoformat(item["session_date"]),
                    instrument=InstrumentKind(item["instrument"]),
                    row_count=item["row_count"],
                    content_hash=item["content_hash"],
                )
                for item in value["partitions"]
            )
            return NormalizedReleaseManifest(
                release_schema_version=value["release_schema_version"],
                dataset_id=value["dataset_id"],
                dataset_version=value["dataset_version"],
                raw_release_id=value["raw_release_id"],
                raw_source=value["raw_source"],
                raw_checksum=value["raw_checksum"],
                raw_schema_version=value["raw_schema_version"],
                normalization_version=value["normalization_version"],
                qc_release_id=value["qc_release_id"],
                qc_status=QCStatus(value["qc_status"]),
                qc_report_hash=value["qc_report_hash"],
                trading_dates=tuple(date.fromisoformat(item) for item in value["trading_dates"]),
                instruments=tuple(InstrumentKind(item) for item in value["instruments"]),
                contract_scope=tuple(value["contract_scope"]),
                partitions=partitions,
                total_rows=value["total_rows"],
                content_hash=value["content_hash"],
                manifest_hash=value["manifest_hash"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ReleaseError(f"invalid release manifest: {error}") from error
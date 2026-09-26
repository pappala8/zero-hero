"""Read-only adapter for the supplied optionsdata.shop Parquet sample."""

from __future__ import annotations

import hashlib
import gc
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from data.models import (
    ContractIdentity,
    InstrumentKind,
    NormalizedBar,
    RawMarketRecord,
    RawReleaseMetadata,
    SessionInfo,
)

IST = ZoneInfo("Asia/Kolkata")
EXPECTED_COLUMNS = (
    "datetime",
    "stock_code",
    "exchange_code",
    "product_type",
    "expiry_date",
    "strike_price",
    "right",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
)
FILENAME_PATTERN = re.compile(r"^NIFTY_OPT_1min_(\d{4}-\d{2}-\d{2})_exp(\d{4}-\d{2}-\d{2})\.parquet$")


@dataclass(frozen=True)
class SampleAdapterConfig:
    normalization_version: str = "OPTIONS_DATA_SHOP_NORMALIZED_V1"
    provider_name: str = "optionsdata.shop"
    source_timezone: str = "Asia/Kolkata"
    bar_interval: timedelta = timedelta(minutes=1)
    session_open: time = time(9, 15)
    source_session_close: time = time(15, 30)
    event_session_close: time = time(15, 31)
    retain_raw_records: bool = True


@dataclass(frozen=True)
class AdapterIssue:
    file_name: str
    row_number: int
    message: str
    raw_values: dict[str, Any]


@dataclass(frozen=True)
class SampleIngestionResult:
    raw_release: RawReleaseMetadata
    raw_records: tuple[RawMarketRecord, ...]
    normalized_records: tuple[NormalizedBar, ...]
    issues: tuple[AdapterIssue, ...]
    files: tuple[str, ...]
    source_row_count: int


class SampleAdapterError(RuntimeError):
    pass


def _parse_expiry(value: Any) -> date:
    return datetime.strptime(str(value), "%d-%b-%Y").date()


def _native_timestamp(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("datetime is not a timestamp")
    if value.tzinfo is not None:
        raise ValueError("native timestamp must be timezone-naive wall-clock IST")
    return value.replace(tzinfo=IST)


def _numeric(value: Any, field: str) -> float:
    if value is None or not isfinite(float(value)):
        raise ValueError(f"{field} is missing or non-finite")
    return float(value)


def _aggregate_hash(files: list[Path]) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_size = 0
    for path in sorted(files, key=lambda item: item.name):
        content = path.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        digest.update(f"{path.name}:{file_hash}\n".encode("utf-8"))
        total_size += len(content)
    return digest.hexdigest(), total_size


def ingest_sample_directory(
    sample_directory: str | Path,
    *,
    retrieval_timestamp: datetime,
    config: SampleAdapterConfig = SampleAdapterConfig(),
    retain_normalized_records: bool = True,
    batch_callback: Optional[Callable[[RawReleaseMetadata, str, tuple[NormalizedBar, ...]], None]] = None,
) -> SampleIngestionResult:
    root = Path(sample_directory)
    if not root.is_dir():
        raise SampleAdapterError(f"sample directory does not exist: {root}")
    files = sorted(root.glob("*.parquet"), key=lambda path: path.name)
    if not files:
        raise SampleAdapterError("sample directory contains no Parquet files")
    if retrieval_timestamp.tzinfo is None or retrieval_timestamp.utcoffset() is None:
        raise SampleAdapterError("retrieval_timestamp must include a timezone")
    aggregate_hash, total_size = _aggregate_hash(files)
    raw_release = RawReleaseMetadata(
        release_id=f"{config.provider_name}-sample-{aggregate_hash[:16]}",
        source=config.provider_name,
        retrieval_timestamp=retrieval_timestamp,
        schema_version="OPTIONS_DATA_SHOP_NATIVE_V1",
        license_reference="sample README: evaluation sample; redistribution not permitted",
        checksum=aggregate_hash,
        file_size=total_size,
        partition_id="sample-directory",
        source_metadata={
            "native_timezone": config.source_timezone,
            "native_bar_time_convention": "bar_start_wall_clock",
            "native_datetime_type": "timestamp[us] timezone-naive",
            "native_exchange_code": "NFO",
            "native_product_type": "Options",
            "event_timestamp_rule": "native bar start + 1 minute",
        },
    )
    raw_records: list[RawMarketRecord] = []
    normalized_records: list[NormalizedBar] = []
    issues: list[AdapterIssue] = []
    source_row_count = 0
    for file_path in files:
        match = FILENAME_PATTERN.match(file_path.name)
        if match is None:
            raise SampleAdapterError(f"unexpected sample filename: {file_path.name}")
        table = pq.read_table(file_path)
        missing = [column for column in EXPECTED_COLUMNS if column not in table.column_names]
        if missing:
            raise SampleAdapterError(f"{file_path.name} missing columns: {missing}")
        rows = table.select(list(EXPECTED_COLUMNS)).to_pylist()
        source_row_count += len(rows)
        batch_records: list[NormalizedBar] = []
        for row_number, row in enumerate(rows, start=1):
            try:
                if row["stock_code"] != "NIFTY" or row["exchange_code"] != "NFO" or row["product_type"] != "Options":
                    raise ValueError("unexpected instrument identity fields")
                start_timestamp = _native_timestamp(row["datetime"])
                session_date = start_timestamp.date()
                expiry = _parse_expiry(row["expiry_date"])
                right = {"Call": "CE", "Put": "PE"}.get(str(row["right"]))
                if right is None:
                    raise ValueError("right must be Call or Put")
                strike = _numeric(row["strike_price"], "strike_price")
                open_ = _numeric(row["open"], "open")
                high = _numeric(row["high"], "high")
                low = _numeric(row["low"], "low")
                close = _numeric(row["close"], "close")
                volume = _numeric(row["volume"], "volume")
                open_interest = _numeric(row["open_interest"], "open_interest")
                source_session = SessionInfo(
                    session_date=session_date,
                    timezone=config.source_timezone,
                    session_open=config.session_open,
                    session_close=config.source_session_close,
                    calendar_id="OPTIONS_DATA_SHOP_SAMPLE_SESSION_V1",
                )
                event_session = SessionInfo(
                    session_date=session_date,
                    timezone=config.source_timezone,
                    session_open=config.session_open,
                    session_close=config.event_session_close,
                    calendar_id="OPTIONS_DATA_SHOP_SAMPLE_SESSION_V1",
                )
                contract = ContractIdentity("NIFTY 50", expiry, strike, right)
                source_id = f"{file_path.name}:{row_number}"
                if config.retain_raw_records:
                    raw_records.append(RawMarketRecord(
                        release=raw_release,
                        source_record_id=source_id,
                        instrument=InstrumentKind.OPTION,
                        contract=contract,
                        timestamp=start_timestamp,
                        session=source_session,
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=volume,
                        open_interest=open_interest,
                    ))
                normalized = NormalizedBar(
                    normalization_version=config.normalization_version,
                    raw_release_id=raw_release.release_id,
                    source_record_id=source_id,
                    instrument=InstrumentKind.OPTION,
                    contract=contract,
                    event_timestamp=start_timestamp + config.bar_interval,
                    session=event_session,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    open_interest=open_interest,
                )
                batch_records.append(normalized)
                if retain_normalized_records:
                    normalized_records.append(normalized)
            except (TypeError, ValueError, OverflowError) as error:
                issues.append(AdapterIssue(file_path.name, row_number, str(error), row))
        if batch_callback is not None:
            batch_callback(raw_release, file_path.name, tuple(batch_records))
        del rows, table, batch_records
        gc.collect()
    return SampleIngestionResult(raw_release, tuple(raw_records), tuple(normalized_records), tuple(issues), tuple(path.name for path in files), source_row_count)
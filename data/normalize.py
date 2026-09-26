"""Lossless raw-to-normalized transformation with no repair or imputation."""

from __future__ import annotations

from typing import Iterable

from .models import NormalizedBar, RawMarketRecord


def normalize_records(
    records: Iterable[RawMarketRecord], normalization_version: str
) -> tuple[NormalizedBar, ...]:
    if not normalization_version:
        raise ValueError("normalization_version is required")
    return tuple(
        NormalizedBar(
            normalization_version=normalization_version,
            raw_release_id=record.release.release_id,
            source_record_id=record.source_record_id,
            instrument=record.instrument,
            contract=record.contract,
            event_timestamp=record.timestamp,
            session=record.session,
            open=record.open,
            high=record.high,
            low=record.low,
            close=record.close,
            volume=record.volume,
            open_interest=record.open_interest,
        )
        for record in records
    )
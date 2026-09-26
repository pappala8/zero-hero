"""Local append-only storage and crash checks for paper-event journals."""

from __future__ import annotations

import hashlib
import json
import copy
import os
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Collection, Iterator, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt.
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX uses fcntl.
    msvcrt = None

from .journal import JournalEntry, JournaledPaperSession, PaperEventJournal
from .ledger import PaperAccountLedger
from .paper import PaperBarEvent, PaperTradingConfig, PaperTradingEngine
from risk import RiskParameters
from strategy import StrategyConfig


JOURNAL_SCHEMA_VERSION = "ZH_PAPER_JOURNAL_V1"


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _configuration_value(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _configuration_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Mapping):
        return {str(key): _configuration_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [_configuration_value(item) for item in value]
    return value


def build_configuration_manifest(
    strategy_config: StrategyConfig,
    risk_parameters: RiskParameters,
    paper_config: PaperTradingConfig,
    expiry_calendar: Mapping[date, Collection[date]] | None = None,
    starting_balance: float | None = None,
) -> "JournalManifest":
    """Build the exact versioned configuration payload required for replay."""

    configuration = {
        "strategy": _configuration_value(strategy_config),
        "risk": _configuration_value(risk_parameters),
        "paper": _configuration_value(paper_config),
    }
    if expiry_calendar is not None:
        configuration["expiry_calendar"] = _configuration_value(expiry_calendar)
    if starting_balance is not None:
        configuration["starting_balance"] = starting_balance
    return JournalManifest.create(
        strategy_config_version=strategy_config.feature_config_version,
        risk_config_version=risk_parameters.risk_config_version,
        configuration=configuration,
    )


class ProcessFileLock:
    """Exclusive OS-level lock for one local journal path."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle = None

    def __enter__(self) -> "ProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        self._handle.seek(0)
        self._handle.write(b"0")
        self._handle.flush()
        self._handle.seek(0)
        if fcntl is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_LOCK, 1)
        else:  # pragma: no cover - one of the platform lock modules is expected.
            raise RuntimeError("no supported process lock implementation")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._handle is None:
            return
        self._handle.seek(0)
        if fcntl is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        self._handle.close()
        self._handle = None


@dataclass(frozen=True)
class JournalManifest:
    journal_schema_version: str
    strategy_config_version: str
    risk_config_version: str
    configuration: Mapping[str, Any]
    configuration_hash: str

    @classmethod
    def create(
        cls,
        *,
        strategy_config_version: str,
        risk_config_version: str,
        configuration: Mapping[str, Any],
        journal_schema_version: str = JOURNAL_SCHEMA_VERSION,
    ) -> "JournalManifest":
        encoded = _canonical_json(configuration)
        return cls(
            journal_schema_version=journal_schema_version,
            strategy_config_version=strategy_config_version,
            risk_config_version=risk_config_version,
            configuration=dict(configuration),
            configuration_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "journal_schema_version": self.journal_schema_version,
            "strategy_config_version": self.strategy_config_version,
            "risk_config_version": self.risk_config_version,
            "configuration": dict(self.configuration),
            "configuration_hash": self.configuration_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "JournalManifest":
        manifest = cls(
            journal_schema_version=value["journal_schema_version"],
            strategy_config_version=value["strategy_config_version"],
            risk_config_version=value["risk_config_version"],
            configuration=value["configuration"],
            configuration_hash=value["configuration_hash"],
        )
        expected = hashlib.sha256(_canonical_json(manifest.configuration).encode("utf-8")).hexdigest()
        if expected != manifest.configuration_hash:
            raise ValueError("journal manifest configuration hash mismatch")
        return manifest


class StorageCheckStatus(str, Enum):
    CLEAN = "CLEAN"
    MISSING_MANIFEST = "MISSING_MANIFEST"
    MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
    PARTIAL_TAIL = "PARTIAL_TAIL"
    INVALID_RECORD = "INVALID_RECORD"
    SEQUENCE_ERROR = "SEQUENCE_ERROR"


@dataclass(frozen=True)
class StorageCheck:
    status: StorageCheckStatus
    entry_count: int
    detail: str = ""


class JournalStorageError(RuntimeError):
    pass


@dataclass
class LocalPaperJournalStorage:
    journal_path: Path
    manifest_path: Path
    manifest: JournalManifest
    lock_path: Path | None = None

    def __post_init__(self) -> None:
        if self.lock_path is None:
            self.lock_path = self.journal_path.with_suffix(self.journal_path.suffix + ".lock")

    @classmethod
    def initialize(
        cls,
        journal_path: str | Path,
        manifest_path: str | Path,
        manifest: JournalManifest,
    ) -> "LocalPaperJournalStorage":
        storage = cls(Path(journal_path), Path(manifest_path), manifest)
        with ProcessFileLock(storage.lock_path):
            storage.journal_path.parent.mkdir(parents=True, exist_ok=True)
            storage.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            if storage.manifest_path.exists():
                existing = storage._read_manifest()
                if existing != manifest:
                    raise JournalStorageError("existing journal manifest does not match requested manifest")
            else:
                storage._write_manifest(manifest)
            storage.journal_path.touch(exist_ok=True)
        return storage

    def _read_manifest(self) -> JournalManifest:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return JournalManifest.from_dict(value)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise JournalStorageError(f"invalid journal manifest: {error}") from error

    def _write_manifest(self, manifest: JournalManifest) -> None:
        content = json.dumps(manifest.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        temporary = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.manifest_path)

    def check(self) -> StorageCheck:
        with ProcessFileLock(self.lock_path):
            return self._check_unlocked()

    def _check_unlocked(self) -> StorageCheck:
        if not self.manifest_path.exists():
            return StorageCheck(StorageCheckStatus.MISSING_MANIFEST, 0, "manifest_missing")
        try:
            if self._read_manifest() != self.manifest:
                return StorageCheck(StorageCheckStatus.MANIFEST_MISMATCH, 0, "manifest_mismatch")
            data = self.journal_path.read_bytes() if self.journal_path.exists() else b""
            if data and not data.endswith(b"\n"):
                return StorageCheck(StorageCheckStatus.PARTIAL_TAIL, data.count(b"\n"), "journal_tail_not_newline_terminated")
            journal = PaperEventJournal.from_json_lines(data.decode("utf-8"))
            return StorageCheck(StorageCheckStatus.CLEAN, len(journal.entries))
        except UnicodeDecodeError as error:
            return StorageCheck(StorageCheckStatus.INVALID_RECORD, 0, f"journal_encoding_invalid: {error}")
        except JournalStorageError as error:
            return StorageCheck(StorageCheckStatus.INVALID_RECORD, 0, str(error))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            detail = str(error)
            status = StorageCheckStatus.SEQUENCE_ERROR if "sequence" in detail else StorageCheckStatus.INVALID_RECORD
            return StorageCheck(status, 0, detail)

    def load(self) -> PaperEventJournal:
        with ProcessFileLock(self.lock_path):
            check = self._check_unlocked()
            if check.status is not StorageCheckStatus.CLEAN:
                raise JournalStorageError(f"journal is not clean: {check.status.value}: {check.detail}")
            content = self.journal_path.read_text(encoding="utf-8") if self.journal_path.exists() else ""
            return PaperEventJournal.from_json_lines(content)

    def append(self, entry: JournalEntry) -> JournalEntry:
        with ProcessFileLock(self.lock_path):
            check = self._check_unlocked()
            if check.status is not StorageCheckStatus.CLEAN:
                raise JournalStorageError(f"cannot append to journal: {check.status.value}: {check.detail}")
            expected = check.entry_count + 1
            if entry.sequence != expected:
                raise JournalStorageError("journal append sequence does not continue existing journal")
            content = json.dumps(entry.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
            with self.journal_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        return entry

    def recover_partial_tail(self) -> StorageCheck:
        with ProcessFileLock(self.lock_path):
            check = self._check_unlocked()
            if check.status is not StorageCheckStatus.PARTIAL_TAIL:
                return check
            data = self.journal_path.read_bytes()
            last_newline = data.rfind(b"\n")
            with self.journal_path.open("r+b") as handle:
                handle.truncate(last_newline + 1)
                handle.flush()
                os.fsync(handle.fileno())
            return self._check_unlocked()

    def recover_session(
        self, ledger_factory: Callable[[], PaperAccountLedger]
    ) -> JournaledPaperSession:
        return JournaledPaperSession.replay(self.load(), ledger_factory)


@dataclass
class StoredPaperSession:
    session: JournaledPaperSession
    storage: LocalPaperJournalStorage

    def ingest(self, event: PaperBarEvent):
        before = copy.deepcopy(self.session)
        result = self.session.ingest(event)
        try:
            self.storage.append(self.session.journal.entries[-1])
        except Exception:
            self.session = before
            raise
        return result

    def close_session(self, session_date, close_timestamp):
        before = copy.deepcopy(self.session)
        result = self.session.close_session(session_date, close_timestamp)
        try:
            self.storage.append(self.session.journal.entries[-1])
        except Exception:
            self.session = before
            raise
        return result

    def report(self):
        from .report import build_paper_session_report

        return build_paper_session_report(self.session, self.storage.manifest)


@dataclass
class ConfiguredPaperSessionFactory:
    """Bind storage, configuration, and replay construction into one boundary."""

    journal_path: str | Path
    manifest_path: str | Path
    strategy_config: StrategyConfig
    risk_parameters: RiskParameters
    paper_config: PaperTradingConfig
    expiry_calendar: Mapping[date, Collection[date]]
    starting_balance: float

    def __post_init__(self) -> None:
        if self.paper_config.risk_parameters != self.risk_parameters:
            raise ValueError("paper_config risk parameters do not match factory risk parameters")

    @property
    def manifest(self) -> JournalManifest:
        return build_configuration_manifest(
            self.strategy_config,
            self.risk_parameters,
            self.paper_config,
            self.expiry_calendar,
            self.starting_balance,
        )

    def _ledger(self) -> PaperAccountLedger:
        from strategy import StrategyEngine

        return PaperAccountLedger(
            PaperTradingEngine(
                strategy_engine=StrategyEngine(self.strategy_config, self.expiry_calendar),
                config=self.paper_config,
            ),
            starting_balance=self.starting_balance,
        )

    def open(self) -> StoredPaperSession:
        storage = LocalPaperJournalStorage.initialize(self.journal_path, self.manifest_path, self.manifest)
        if storage.check().entry_count:
            session = storage.recover_session(self._ledger)
        else:
            session = JournaledPaperSession(self._ledger())
        return StoredPaperSession(session, storage)

    def recover(self) -> StoredPaperSession:
        storage = LocalPaperJournalStorage.initialize(self.journal_path, self.manifest_path, self.manifest)
        session = storage.recover_session(self._ledger)
        return StoredPaperSession(session, storage)
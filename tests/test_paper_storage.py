from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from execution import (
    ConfiguredPaperSessionFactory,
    ProcessFileLock,
    JournalManifest,
    JournalStorageError,
    JournaledPaperSession,
    LocalPaperJournalStorage,
    PaperAccountLedger,
    PaperEventJournal,
    StorageCheckStatus,
    StoredPaperSession,
    build_configuration_manifest,
)

from fixtures import SESSION_DATE, timestamp
from test_paper_trading import paper_engine, paper_event


def manifest() -> JournalManifest:
    return JournalManifest.create(
        strategy_config_version="TEST_STRATEGY_V1",
        risk_config_version="TEST_RISK_V1",
        configuration={"forced_eod": "15:30", "threshold": None},
    )


def storage(directory: str) -> LocalPaperJournalStorage:
    root = Path(directory)
    return LocalPaperJournalStorage.initialize(root / "events.jsonl", root / "manifest.json", manifest())


def stored_session(store: LocalPaperJournalStorage) -> StoredPaperSession:
    return StoredPaperSession(JournaledPaperSession(PaperAccountLedger(paper_engine(), 1000.0)), store)


def configured_factory(directory: str, *, strategy_config=None) -> ConfiguredPaperSessionFactory:
    engine = paper_engine()
    root = Path(directory)
    return ConfiguredPaperSessionFactory(
        journal_path=root / "events.jsonl",
        manifest_path=root / "manifest.json",
        strategy_config=strategy_config or engine.strategy_engine.config,
        risk_parameters=engine.config.risk_parameters,
        paper_config=engine.config,
        expiry_calendar=engine.strategy_engine.expiry_calendar,
        starting_balance=1000.0,
    )


class PaperStorageTests(unittest.TestCase):
    def test_factory_binds_matching_configuration_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = configured_factory(directory)
            session = factory.open()

            self.assertEqual(session.storage.manifest, factory.manifest)
            self.assertEqual(session.storage.manifest.configuration["expiry_calendar"], {"2026-01-05": ["2026-01-08"]})
            self.assertEqual(session.storage.manifest.configuration["starting_balance"], 1000.0)

    def test_factory_rejects_configuration_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = configured_factory(directory)
            factory.open().ingest(paper_event(timestamp(9, 35)))
            changed = replace(factory.strategy_config, breakout_buffer=2.0)
            mismatch = configured_factory(directory, strategy_config=changed)

            with self.assertRaises(JournalStorageError):
                mismatch.recover()

    def test_factory_rejects_manifest_tampering_during_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = configured_factory(directory)
            session = factory.open()
            value = json.loads(session.storage.manifest_path.read_text(encoding="utf-8"))
            value["configuration_hash"] = "tampered"
            session.storage.manifest_path.write_text(json.dumps(value) + "\n", encoding="utf-8")

            with self.assertRaises(JournalStorageError):
                factory.recover()

    def test_factory_recovers_account_and_replay_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = configured_factory(directory)
            first = factory.open()
            first.ingest(paper_event(timestamp(9, 35)))
            first.ingest(paper_event(timestamp(15, 30), price=12.0))
            first.close_session(SESSION_DATE, timestamp(15, 30))
            original_journal = first.session.journal.to_json_lines()

            recovered = factory.recover()
            reopened = factory.open()

            self.assertEqual(recovered.session.journal.to_json_lines(), original_journal)
            self.assertEqual(recovered.session.ledger.snapshot().balance, first.session.ledger.snapshot().balance)
            self.assertEqual(reopened.session.journal.to_json_lines(), original_journal)
    def test_configuration_manifest_captures_exact_configs(self) -> None:
        engine = paper_engine()
        built = build_configuration_manifest(
            engine.strategy_engine.config,
            engine.config.risk_parameters,
            engine.config,
        )

        self.assertEqual(built.strategy_config_version, engine.strategy_engine.config.feature_config_version)
        self.assertEqual(built.risk_config_version, engine.config.risk_parameters.risk_config_version)
        self.assertEqual(set(built.configuration), {"paper", "risk", "strategy"})
        self.assertEqual(
            built.configuration["strategy"]["breakout_buffer"],
            engine.strategy_engine.config.breakout_buffer,
        )
        self.assertEqual(
            built.configuration["paper"]["paper_config_version"],
            engine.config.paper_config_version,
        )

    def test_process_lock_uses_sidecar_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.lock"
            with ProcessFileLock(path):
                self.assertTrue(path.exists())

    def test_manifest_and_append_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = storage(directory)
            session = stored_session(store)
            session.ingest(paper_event(timestamp(9, 35)))

            self.assertEqual(store.check().status, StorageCheckStatus.CLEAN)
            self.assertEqual(store.check().entry_count, 1)
            self.assertEqual(store.load().to_json_lines(), store.journal_path.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(store.manifest_path.read_text(encoding="utf-8"))["strategy_config_version"], "TEST_STRATEGY_V1")

    def test_manifest_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = storage(directory)
            mismatched = JournalManifest.create(
                strategy_config_version="OTHER_STRATEGY_V1",
                risk_config_version="TEST_RISK_V1",
                configuration={"forced_eod": "15:30", "threshold": None},
            )
            with self.assertRaises(JournalStorageError):
                LocalPaperJournalStorage.initialize(store.journal_path, store.manifest_path, mismatched)

    def test_manifest_tampering_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = storage(directory)
            value = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            value["configuration_hash"] = "tampered"
            store.manifest_path.write_text(json.dumps(value) + "\n", encoding="utf-8")

            check = store.check()

            self.assertEqual(check.status, StorageCheckStatus.INVALID_RECORD)

    def test_partial_tail_is_detected_and_explicitly_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = storage(directory)
            stored_session(store).ingest(paper_event(timestamp(9, 35)))
            with store.journal_path.open("ab") as handle:
                handle.write(b'{"incomplete":')

            self.assertEqual(store.check().status, StorageCheckStatus.PARTIAL_TAIL)
            with self.assertRaises(JournalStorageError):
                store.load()
            recovered = store.recover_partial_tail()

            self.assertEqual(recovered.status, StorageCheckStatus.CLEAN)
            self.assertEqual(recovered.entry_count, 1)

    def test_malformed_complete_record_is_not_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = storage(directory)
            store.journal_path.write_text('{"broken":true}\n', encoding="utf-8")

            check = store.check()

            self.assertEqual(check.status, StorageCheckStatus.INVALID_RECORD)
            with self.assertRaises(JournalStorageError):
                store.load()

    def test_storage_recovery_replays_same_account_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = storage(directory)
            session = stored_session(store)
            session.ingest(paper_event(timestamp(9, 35)))
            session.ingest(paper_event(timestamp(15, 30), price=12.0))
            session.close_session(SESSION_DATE, timestamp(15, 30))

            recovered = store.recover_session(lambda: PaperAccountLedger(paper_engine(), 1000.0))

            self.assertEqual(recovered.ledger.snapshot().balance, session.session.ledger.snapshot().balance)
            self.assertEqual(recovered.ledger.snapshot().session_closed, True)
            self.assertEqual(recovered.journal.to_json_lines(), session.session.journal.to_json_lines())


if __name__ == "__main__":
    unittest.main()
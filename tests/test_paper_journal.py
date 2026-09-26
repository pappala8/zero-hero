from __future__ import annotations

import json
import unittest

from execution import (
    JournaledPaperSession,
    PaperAccountLedger,
    PaperEventJournal,
    ReplayMismatchError,
    PositionState,
)

from fixtures import SESSION_DATE, timestamp
from test_paper_trading import paper_engine, paper_event


def new_session() -> JournaledPaperSession:
    return JournaledPaperSession(PaperAccountLedger(paper_engine(), 1000.0))


class PaperJournalTests(unittest.TestCase):
    def test_session_close_requires_forced_eod_and_flat_position(self) -> None:
        session = new_session()
        session.ingest(paper_event(timestamp(9, 35)))

        before_eod = session.close_session(SESSION_DATE, timestamp(15, 0))
        while_open = session.close_session(SESSION_DATE, timestamp(15, 30))
        session.ingest(paper_event(timestamp(15, 30), price=12.0))
        closed = session.close_session(SESSION_DATE, timestamp(15, 30))

        self.assertEqual(before_eod.reasons, ("session_close_before_forced_eod",))
        self.assertEqual(while_open.reasons, ("open_position_requires_forced_eod_event",))
        self.assertTrue(closed.accepted)
        self.assertTrue(closed.snapshot.session_closed)
        self.assertEqual(closed.snapshot.position_state, PositionState.FLAT)

    def test_journal_append_failure_rolls_back_ledger(self) -> None:
        session = new_session()
        before = session.ledger.snapshot()
        original = session.journal.append

        def fail(entry):
            raise OSError("simulated journal failure")

        session.journal.append = fail
        with self.assertRaises(OSError):
            session.ingest(paper_event(timestamp(9, 35)))
        self.assertEqual(session.ledger.snapshot(), before)
        session.journal.append = original
        self.assertEqual(len(session.journal.entries), 0)

    def test_json_lines_are_canonical_and_recover_equivalent_state(self) -> None:
        session = new_session()
        session.ingest(paper_event(timestamp(9, 35)))
        session.ingest(paper_event(timestamp(9, 40), price=12.0))
        session.ingest(paper_event(timestamp(15, 30), price=12.0))
        session.close_session(SESSION_DATE, timestamp(15, 30))

        content = session.journal.to_json_lines()
        restored_journal = PaperEventJournal.from_json_lines(content)
        recovered = JournaledPaperSession.replay(
            restored_journal,
            lambda: PaperAccountLedger(paper_engine(), 1000.0),
        )

        self.assertEqual(content, restored_journal.to_json_lines())
        self.assertEqual(recovered.journal.to_json_lines(), content)
        self.assertEqual(recovered.ledger.snapshot().balance, session.ledger.snapshot().balance)
        self.assertEqual(recovered.ledger.snapshot().session_closed, True)

    def test_replay_preserves_rejected_duplicate_event(self) -> None:
        session = new_session()
        event = paper_event(timestamp(9, 35))
        session.ingest(event)
        duplicate = session.ingest(event)

        self.assertFalse(duplicate.accepted)
        recovered = JournaledPaperSession.recover_json_lines(
            session.journal.to_json_lines(),
            lambda: PaperAccountLedger(paper_engine(), 1000.0),
        )
        self.assertEqual(recovered.journal.to_json_lines(), session.journal.to_json_lines())
        self.assertEqual(recovered.ledger.last_event_timestamp, timestamp(9, 35))

    def test_replay_detects_tampered_outcome(self) -> None:
        session = new_session()
        session.ingest(paper_event(timestamp(9, 35)))
        lines = session.journal.to_json_lines().splitlines()
        value = json.loads(lines[0])
        value["accepted"] = False
        tampered = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"

        with self.assertRaises(ReplayMismatchError):
            JournaledPaperSession.recover_json_lines(
                tampered,
                lambda: PaperAccountLedger(paper_engine(), 1000.0),
            )

    def test_append_sequence_must_be_contiguous(self) -> None:
        journal = PaperEventJournal()
        session = new_session()
        session.ingest(paper_event(timestamp(9, 35)))
        entry = session.journal.entries[0]

        with self.assertRaises(ValueError):
            journal.append(entry.__class__(
                sequence=2,
                operation=entry.operation,
                timestamp=entry.timestamp,
                session_date=entry.session_date,
                event=entry.event,
                accepted=entry.accepted,
                reasons=entry.reasons,
                snapshot=entry.snapshot,
            ))


if __name__ == "__main__":
    unittest.main()
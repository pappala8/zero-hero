from __future__ import annotations

import tempfile
import unittest

from fixtures import SESSION_DATE, timestamp
from test_paper_storage import configured_factory
from test_paper_trading import paper_event


class PaperReportTests(unittest.TestCase):
    def test_completed_session_report_reconciles_and_counts_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = configured_factory(directory).open()
            event = paper_event(timestamp(9, 35))
            session.ingest(event)
            session.ingest(event)
            session.ingest(paper_event(timestamp(15, 30), price=12.0))
            session.close_session(SESSION_DATE, timestamp(15, 30))

            report = session.report()

            self.assertTrue(report.reconciliation_ok)
            self.assertEqual(report.entry_count, 4)
            self.assertEqual(report.event_count, 3)
            self.assertEqual(report.session_close_count, 1)
            self.assertEqual(report.rejected_operations, 1)
            self.assertEqual(report.rejected_reason_counts, (("duplicate_event_timestamp", 1),))
            self.assertEqual(report.trade_count, 1)
            self.assertEqual(report.closed_sessions, ("2026-01-05",))

    def test_report_marks_open_position_and_session_as_unreconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = configured_factory(directory).open()
            session.ingest(paper_event(timestamp(9, 35)))

            report = session.report()

            self.assertFalse(report.reconciliation_ok)
            self.assertEqual(report.final_position_state, "OPEN")
            self.assertIn("open_position_unreconciled", report.reconciliation_reasons)
            self.assertIn("session_not_closed", report.reconciliation_reasons)

    def test_recovered_report_is_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = configured_factory(directory)
            session = factory.open()
            session.ingest(paper_event(timestamp(9, 35)))
            session.ingest(paper_event(timestamp(15, 30), price=12.0))
            session.close_session(SESSION_DATE, timestamp(15, 30))

            original = session.report()
            recovered = factory.recover().report()

            self.assertEqual(recovered.to_json(), original.to_json())


if __name__ == "__main__":
    unittest.main()
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime

from execution import IterablePaperEventSource, PaperAccountLedger, PositionState

from fixtures import SESSION_DATE, timestamp
from test_paper_trading import paper_engine, paper_event


NEXT_SESSION = date(2026, 1, 6)


class PaperLedgerTests(unittest.TestCase):
    def test_account_reconciliation_after_forced_exit(self) -> None:
        account = PaperAccountLedger(paper_engine(), 1000.0)
        entry = account.ingest(paper_event(timestamp(9, 35)))
        exit_result = account.ingest(paper_event(timestamp(15, 30), price=12.0))

        self.assertTrue(entry.accepted)
        self.assertEqual(entry.snapshot.balance, 1000.0)
        self.assertAlmostEqual(entry.snapshot.equity, 991.0)
        self.assertEqual(exit_result.snapshot.balance, 1144.0)
        self.assertAlmostEqual(exit_result.snapshot.realized_pnl, 144.0)
        self.assertAlmostEqual(exit_result.snapshot.equity, 1144.0)
        self.assertAlmostEqual(exit_result.snapshot.daily_pnl, 144.0)
        self.assertEqual(exit_result.snapshot.position_state, PositionState.FLAT)

    def test_day_rollover_resets_daily_counters_when_flat(self) -> None:
        account = PaperAccountLedger(paper_engine(), 1000.0)
        account.ingest(paper_event(timestamp(9, 35)))
        account.ingest(paper_event(timestamp(15, 30), price=12.0))
        next_day = account.ingest(paper_event(timestamp(9, 35, NEXT_SESSION), session_date=NEXT_SESSION))

        self.assertTrue(next_day.accepted)
        self.assertEqual(next_day.snapshot.session_date, NEXT_SESSION)
        self.assertEqual(next_day.snapshot.daily_pnl, 0.0)
        self.assertEqual(next_day.snapshot.trades_today, 0)
        self.assertEqual(next_day.snapshot.consecutive_losses, 0)
        self.assertEqual(next_day.snapshot.balance, 1144.0)

    def test_open_position_blocks_cross_session_event(self) -> None:
        account = PaperAccountLedger(paper_engine(), 1000.0)
        account.ingest(paper_event(timestamp(9, 35)))
        next_day = account.ingest(paper_event(timestamp(9, 35, NEXT_SESSION), session_date=NEXT_SESSION))

        self.assertFalse(next_day.accepted)
        self.assertEqual(next_day.reasons, ("open_position_crosses_session_boundary",))
        self.assertEqual(next_day.snapshot.position_state, PositionState.OPEN)

    def test_duplicate_and_out_of_order_events_are_rejected(self) -> None:
        account = PaperAccountLedger(paper_engine(), 1000.0)
        first = paper_event(timestamp(9, 35))
        accepted = account.ingest(first)
        duplicate = account.ingest(first)
        earlier = account.ingest(paper_event(timestamp(9, 34)))

        self.assertTrue(accepted.accepted)
        self.assertEqual(duplicate.reasons, ("duplicate_event_timestamp",))
        self.assertEqual(earlier.reasons, ("out_of_order_event_timestamp",))
        self.assertEqual(account.last_event_timestamp, timestamp(9, 35))

    def test_invalid_event_data_is_rejected_before_strategy(self) -> None:
        account = PaperAccountLedger(paper_engine(), 1000.0)
        invalid_price = replace(paper_event(timestamp(9, 35)), current_option_price=replace(paper_event(timestamp(9, 35)).current_option_price, price=float("nan")))
        invalid_timestamp = replace(
            paper_event(timestamp(9, 36)), timestamp=datetime(2026, 1, 5, 9, 36)
        )
        missing_bars = replace(paper_event(timestamp(9, 37)), underlying_bars=())

        self.assertEqual(account.ingest(invalid_price).reasons, ("current_option_price_invalid",))
        self.assertEqual(account.ingest(invalid_timestamp).reasons, ("event_timestamp_timezone_missing",))
        self.assertEqual(account.ingest(missing_bars).reasons, ("underlying_bar_data_missing",))
        self.assertIsNone(account.last_event_timestamp)

    def test_daily_trade_limit_is_applied_by_ledger_engine(self) -> None:
        account = PaperAccountLedger(paper_engine(), 1000.0)
        account.engine.trades_today = 3
        result = account.ingest(paper_event(timestamp(9, 35)))

        self.assertTrue(result.accepted)
        self.assertEqual(result.reasons, ("maximum_trades_reached",))
        self.assertEqual(result.snapshot.trades_today, 3)

    def test_run_accepts_iterable_event_source(self) -> None:
        account = PaperAccountLedger(paper_engine(), 1000.0)
        source = IterablePaperEventSource((paper_event(timestamp(9, 35)),))
        results = account.run(source)

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].accepted)
        self.assertEqual(results[0].snapshot.position_state, PositionState.OPEN)


if __name__ == "__main__":
    unittest.main()
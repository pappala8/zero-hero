from __future__ import annotations

import unittest
from datetime import datetime, time
from zoneinfo import ZoneInfo

from features import calculate_atr, calculate_opening_range, calculate_or_atr, calculate_vwap

from fixtures import SESSION_DATE, market_bars, timestamp, volume_bars


class FeatureTests(unittest.TestCase):
    def test_opening_range_requires_final_bar_event(self) -> None:
        bars = market_bars()
        incomplete = calculate_opening_range(bars, SESSION_DATE, timestamp(9, 29))
        complete = calculate_opening_range(bars, SESSION_DATE, timestamp(9, 30))

        self.assertFalse(incomplete.available)
        self.assertEqual(incomplete.reason, "opening_range_incomplete")
        self.assertTrue(complete.available)
        self.assertEqual(complete.available_at, timestamp(9, 30))
        self.assertEqual(complete.high, 106.0)
        self.assertEqual(complete.low, 101.0)
        self.assertEqual(complete.width, 5.0)

    def test_future_bar_does_not_change_opening_range(self) -> None:
        bars = market_bars()
        future = bars + [bars[-1].__class__(timestamp(9, 40), 110, 999, 1, 110)]
        original = calculate_opening_range(bars, SESSION_DATE, timestamp(9, 35))
        with_future = calculate_opening_range(future, SESSION_DATE, timestamp(9, 35))
        self.assertEqual(original, with_future)

    def test_atr_requires_warmup_and_or_atr_rejects_unavailable(self) -> None:
        bars = market_bars()
        missing = calculate_atr(bars[:3], timestamp(9, 10), lookback=3, method="sma")
        ready = calculate_atr(bars, timestamp(9, 35), lookback=3, method="sma")
        opening_range = calculate_opening_range(bars, SESSION_DATE, timestamp(9, 30))

        self.assertFalse(missing.available)
        self.assertEqual(missing.reason, "atr_warmup_incomplete")
        self.assertTrue(ready.available)
        self.assertTrue(calculate_or_atr(opening_range, ready).available)

    def test_vwap_requires_explicit_volume_source(self) -> None:
        bars = market_bars()
        unavailable = calculate_vwap(bars, bars, SESSION_DATE, timestamp(9, 35), None, time(9, 15))
        available = calculate_vwap(
            bars, volume_bars(bars), SESSION_DATE, timestamp(9, 35), "nifty_futures", time(9, 15)
        )

        self.assertFalse(unavailable.available)
        self.assertEqual(unavailable.reason, "vwap_volume_source_unspecified")
        self.assertTrue(available.available)
        self.assertIsNotNone(available.value)

    def test_naive_event_timestamp_is_rejected(self) -> None:
        result = calculate_opening_range(
            market_bars(), SESSION_DATE, datetime(2026, 1, 5, 9, 30)
        )
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "event_timestamp_timezone_missing")


if __name__ == "__main__":
    unittest.main()
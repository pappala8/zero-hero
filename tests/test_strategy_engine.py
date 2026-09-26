from __future__ import annotations

import unittest
from datetime import time

from strategy import Direction, Mode, SignalState, StrategyConfig, StrategyEngine

from fixtures import EXPIRY, SESSION_DATE, market_bars, option_candidates, score_values, timestamp, volume_bars, underlying_reference


def config(score_threshold: float | None = 50.0, require_vwap: bool = True) -> StrategyConfig:
    return StrategyConfig(
        breakout_buffer=1.0,
        atr_lookback=3,
        atr_method="sma",
        score_threshold=score_threshold,
        vwap_volume_source="nifty_futures",
        require_vwap_alignment=require_vwap,
        strike_step=5.0,
        strike_mapping_version="TEST_STRIKE_MAP_V1",
        option_required_fields=("premium", "volume", "oi"),
        session_start=time(9, 15),
        session_end=time(15, 30),
        selection_priority=(0, 1, 2),
        confirmation_window_bars=2,
        confirmation_bars_required=1,
    )


def engine(score_threshold: float | None = 50.0, require_vwap: bool = True) -> StrategyEngine:
    return StrategyEngine(config(score_threshold, require_vwap), {SESSION_DATE: (EXPIRY,)})


class StrategyEngineTests(unittest.TestCase):
    def evaluate(self, **kwargs):
        bars = kwargs.pop("bars", market_bars())
        direction = kwargs.pop("direction", "CE")
        return engine(kwargs.pop("score_threshold", 50.0), kwargs.pop("require_vwap", True)).evaluate(
            session_date=SESSION_DATE,
            event_timestamp=kwargs.pop("event_timestamp", timestamp(9, 35)),
            mode=kwargs.pop("mode", Mode.DAILY),
            underlying_bars=bars,
            volume_bars=volume_bars(bars),
            score_components=kwargs.pop("score_components", score_values()),
            option_candidates=kwargs.pop("option_candidates", option_candidates(direction)),
            selected_expiry=EXPIRY,
            underlying_reference=kwargs.pop("underlying_reference", underlying_reference(timestamp(9, 35))),
        )

    def test_upside_breakout_produces_buy_call(self) -> None:
        signal = self.evaluate()
        self.assertEqual(signal.signal_state, SignalState.BUY_CALL)
        self.assertEqual(signal.direction, Direction.CALL)
        self.assertEqual(signal.selected_contract.strike, 100.0)
        self.assertEqual(len(signal.candidate_contracts), 3)
        self.assertEqual(signal.zh_score, 100.0)


    def test_breakout_requires_confirmation_within_window(self) -> None:
        bars = market_bars(final_close=110.0)
        bars[-1] = bars[-1].__class__(timestamp=bars[-1].timestamp, open=100.0, high=100.5, low=99.5, close=100.5, volume=100.0, instrument=bars[-1].instrument, volume_source=bars[-1].volume_source)
        signal = self.evaluate(bars=bars)
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("breakout_confirmation_not_met", signal.rejection_reasons)

    def test_failed_breakout_is_invalidated(self) -> None:
        bars = market_bars(final_close=99.0)
        last = bars[-1]
        bars[-1] = last.__class__(timestamp=timestamp(9, 35), open=104.0, high=110.0, low=103.0, close=108.0, volume=100.0, instrument=last.instrument, volume_source=last.volume_source)
        bars.append(last.__class__(timestamp=timestamp(9, 40), open=108.0, high=109.0, low=99.0, close=104.0, volume=100.0, instrument=last.instrument, volume_source=last.volume_source))
        candidates = option_candidates("CE", timestamp(9, 40))
        cfg = StrategyConfig(**{**config().__dict__, "confirmation_window_bars": 3, "confirmation_bars_required": 2, "require_vwap_alignment": False})
        signal = StrategyEngine(cfg, {SESSION_DATE: (EXPIRY,)}).evaluate(
            session_date=SESSION_DATE, event_timestamp=timestamp(9, 40), mode=Mode.DAILY,
            underlying_bars=bars, volume_bars=volume_bars(bars), score_components=score_values(),
            option_candidates=candidates, selected_expiry=EXPIRY, underlying_reference=underlying_reference(timestamp(9, 40)),
        )
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("failed_breakout_invalidated", signal.rejection_reasons)

    def test_consecutive_confirmation_bars_are_supported(self) -> None:
        cfg = StrategyConfig(**{**config().__dict__, "confirmation_window_bars": 3, "confirmation_bars_required": 2})
        bars = market_bars(final_close=110.0)
        last = bars[-1]
        bars[-1] = last.__class__(timestamp=timestamp(9, 35), open=105.0, high=108.0, low=104.0, close=108.0, volume=100.0, instrument=last.instrument, volume_source=last.volume_source)
        bars.append(last.__class__(timestamp=timestamp(9, 40), open=107.0, high=111.0, low=106.0, close=110.0, volume=100.0, instrument=last.instrument, volume_source=last.volume_source))
        eng = StrategyEngine(cfg, {SESSION_DATE: (EXPIRY,)})
        signal = eng.evaluate(
            session_date=SESSION_DATE, event_timestamp=timestamp(9, 40), mode=Mode.DAILY,
            underlying_bars=bars, volume_bars=volume_bars(bars),
            score_components=score_values(), option_candidates=option_candidates("CE", timestamp(9, 40)),
            selected_expiry=EXPIRY, underlying_reference=underlying_reference(timestamp(9, 40)),
        )
        self.assertEqual(signal.signal_state, SignalState.BUY_CALL)

    def test_downside_breakout_produces_buy_put(self) -> None:
        bars = market_bars(final_close=90.0)
        signal = self.evaluate(bars=bars, direction="PE")
        self.assertEqual(signal.signal_state, SignalState.BUY_PUT)
        self.assertEqual(signal.direction, Direction.PUT)
        self.assertEqual(signal.selected_contract.strike, 100.0)

    def test_missing_score_evidence_fails_closed(self) -> None:
        signal = self.evaluate(score_components=score_values(volume=None))
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("required_score_evidence_missing_or_invalid", signal.rejection_reasons)

    def test_unconfigured_threshold_is_review_not_trade(self) -> None:
        signal = self.evaluate(score_threshold=None)
        self.assertEqual(signal.signal_state, SignalState.REVIEW)
        self.assertIsNone(signal.selected_contract)

    def test_missing_vwap_fails_required_direction_gate(self) -> None:
        bars = market_bars()
        signal = engine(50.0, True).evaluate(
            session_date=SESSION_DATE,
            event_timestamp=timestamp(9, 35),
            mode=Mode.DAILY,
            underlying_bars=bars,
            volume_bars=None,
            score_components=score_values(),
            option_candidates=option_candidates("CE"),
            selected_expiry=EXPIRY,
            underlying_reference=underlying_reference(timestamp(9, 35)),
        )
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("vwap_volume_data_missing", signal.rejection_reasons)

    def test_event_outside_session_is_rejected(self) -> None:
        signal = self.evaluate(event_timestamp=timestamp(16, 0))
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("event_outside_configured_session", signal.rejection_reasons)

    def test_contradictory_vwap_evidence_is_rejected(self) -> None:
        bars = market_bars(final_close=110.0)
        bars.insert(-1, bars[0].__class__(
            timestamp(9, 32), 120.0, 120.0, 120.0, 120.0,
            volume=100.0, volume_source="nifty_futures"
        ))
        contradictory_volume = [bar.__class__(
            bar.timestamp, bar.open, bar.high, bar.low, bar.close,
            volume=100000.0 if bar.timestamp == timestamp(9, 32) else bar.volume,
            instrument="NIFTY Futures", volume_source="nifty_futures"
        ) for bar in bars]
        signal = engine(50.0, False).evaluate(
            session_date=SESSION_DATE,
            event_timestamp=timestamp(9, 35),
            mode=Mode.DAILY,
            underlying_bars=bars,
            volume_bars=contradictory_volume,
            score_components=score_values(),
            option_candidates=option_candidates("CE"),
            selected_expiry=EXPIRY,
            underlying_reference=underlying_reference(timestamp(9, 35)),
        )
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("vwap_direction_not_aligned", signal.rejection_reasons)

    def test_missing_option_quality_fails_closed(self) -> None:
        candidates = [
            candidate.__class__(
                underlying=candidate.underlying,
                expiry=candidate.expiry,
                strike=candidate.strike,
                right=candidate.right,
                timestamp=candidate.timestamp,
                available_at=candidate.available_at,
                source=candidate.source,
                premium=None,
                volume=candidate.volume,
                oi=candidate.oi,
            )
            for candidate in option_candidates("CE")
        ]
        signal = self.evaluate(option_candidates=candidates)
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("option_quality_evidence_missing", signal.rejection_reasons)

    def test_future_option_snapshot_is_not_selected(self) -> None:
        candidates = option_candidates("CE", timestamp(9, 40))
        signal = self.evaluate(option_candidates=candidates)
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("option_candidate_unavailable_at_signal_timestamp", signal.rejection_reasons)

    def test_latest_causal_option_snapshot_is_selected(self) -> None:
        candidates = option_candidates("CE", timestamp(9, 31)) + option_candidates("CE", timestamp(9, 35))
        signal = self.evaluate(option_candidates=candidates)
        self.assertEqual(signal.signal_state, SignalState.BUY_CALL)
        self.assertEqual(signal.selected_contract.timestamp, timestamp(9, 35))

    def test_future_available_at_is_not_causal_even_if_timestamp_is_old(self) -> None:
        candidates = option_candidates("CE", timestamp(9, 35))
        candidate = candidates[0].__class__(**{**candidates[0].__dict__, "available_at": timestamp(9, 36)})
        signal = self.evaluate(option_candidates=[candidate])
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("option_candidate_unavailable_at_signal_timestamp", signal.rejection_reasons)

    def test_expiry_must_be_in_supplied_historical_calendar(self) -> None:
        signal = StrategyEngine(config(), {SESSION_DATE: ()}).evaluate(
            session_date=SESSION_DATE,
            event_timestamp=timestamp(9, 35),
            mode=Mode.EXPIRY,
            underlying_bars=market_bars(),
            volume_bars=volume_bars(market_bars()),
            score_components=score_values(),
            option_candidates=option_candidates("CE"),
            selected_expiry=EXPIRY,
            underlying_reference=underlying_reference(timestamp(9, 35)),
        )
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("expiry_not_in_historical_calendar", signal.rejection_reasons)

    def test_no_trade_mode_never_forces_signal(self) -> None:
        signal = self.evaluate(mode=Mode.NO_TRADE)
        self.assertEqual(signal.signal_state, SignalState.NO_TRADE)
        self.assertIn("no_trade_capital_protection_mode", signal.rejection_reasons)


if __name__ == "__main__":
    unittest.main()
class OpeningRangeBoundaryTests(unittest.TestCase):
    def test_or_final_bar_is_not_used_as_breakout_confirmation(self) -> None:
        from fixtures import EXPIRY, SESSION_DATE, market_bars, option_candidates, score_values, volume_bars
        engine = StrategyEngine(config(), {SESSION_DATE: (EXPIRY,)})
        bars = market_bars(session_date=SESSION_DATE)
        result = engine.evaluate(
            session_date=SESSION_DATE,
            event_timestamp=timestamp(9, 30),
            mode=Mode.DAILY,
            underlying_bars=bars,
            volume_bars=volume_bars(bars),
            score_components=score_values(),
            option_candidates=option_candidates("CE", session_date=SESSION_DATE),
            selected_expiry=EXPIRY,
            underlying_reference=underlying_reference(timestamp(9, 30)),
        )
        self.assertIn(result.rejection_reasons[0], {"confirmation_window_unavailable", "opening_range_unavailable"})

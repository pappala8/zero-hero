from __future__ import annotations

import unittest
from datetime import time, timedelta

from execution import PaperBarEvent, PaperTradingConfig, PaperTradingEngine, PositionState
from risk import RiskParameters
from strategy import Mode, StrategyEngine

from fixtures import EXPIRY, SESSION_DATE, market_bars, option_candidates, score_values, timestamp, volume_bars, underlying_reference, option_price_snapshot
from test_strategy_engine import config


def paper_config(*, time_exit_after: timedelta | None = None) -> PaperTradingConfig:
    return PaperTradingConfig(
        risk_parameters=RiskParameters(
            risk_fraction=0.1,
            daily_loss_limit=100.0,
            max_trades_per_day=3,
            consecutive_loss_limit=2,
            emergency_premium_loss=3.0,
            structural_stop_rule_version="TEST_STOP_V1",
            time_exit_rule_version="TEST_TIME_EXIT_V1",
            forced_eod_rule_version="TEST_EOD_V1",
        ),
        entry_slippage_per_unit=0.1,
        exit_slippage_per_unit=0.1,
        entry_charges_per_lot=1.0,
        exit_charges_per_lot=1.0,
        forced_eod_time=time(15, 30),
        time_exit_after=time_exit_after,
        exit_precedence=("structural_stop", "emergency_premium_loss", "time_exit", "forced_eod"),
    )


def paper_event(
    event_timestamp,
    *,
    session_date=SESSION_DATE,
    price=10.0,
    stop_risk=2.0,
    mode=Mode.DAILY,
    structural_stop=False,
    volume=True,
    bars=None,
):
    market = tuple(bars or market_bars(session_date=session_date))
    return PaperBarEvent(
        timestamp=event_timestamp,
        session_date=session_date,
        mode=mode,
        underlying_bars=market,
        volume_bars=tuple(volume_bars(market)) if volume else (),
        score_components=score_values(),
        option_candidates=tuple(option_candidates("CE", session_date=session_date)),
        selected_expiry=EXPIRY,
        underlying_reference=underlying_reference(event_timestamp),
        current_option_price=option_price_snapshot(event_timestamp, price=price),
        lot_size=10,
        stop_risk_per_unit=stop_risk,
        structural_stop_triggered=structural_stop,
    )


def paper_engine(*, time_exit_after=None) -> PaperTradingEngine:
    return PaperTradingEngine(
        StrategyEngine(config(), {SESSION_DATE: (EXPIRY,)}),
        paper_config(time_exit_after=time_exit_after),
    )


class PaperTradingTests(unittest.TestCase):
    def test_entry_creates_order_intent_and_open_position(self) -> None:
        engine = paper_engine()
        result = engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)

        self.assertEqual(result.state, PositionState.OPEN)
        self.assertIsNotNone(result.order_intent)
        self.assertEqual(result.order_intent.quantity, 90)
        self.assertEqual(engine.position.entry_price, 10.1)
        self.assertEqual(engine.trades_today, 1)
        self.assertEqual(engine.position.structural_stop_level, 101.0)

    def test_open_position_does_not_reenter_and_holds_without_exit(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        result = engine.process(paper_event(timestamp(9, 40), price=12.0), capital_available=1000.0, equity=1000.0)

        self.assertEqual(result.state, PositionState.OPEN)
        self.assertIsNone(result.order_intent)
        self.assertIsNone(result.trade_record)

    def test_structural_stop_simulates_exit_and_records_pnl(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        bars = list(market_bars(session_date=SESSION_DATE))
        bars[-1] = type(bars[-1])(bars[-1].timestamp, bars[-1].open, bars[-1].high, 99.0, bars[-1].close, bars[-1].volume)
        result = engine.process(
            paper_event(timestamp(9, 40), price=8.0, bars=bars),
            capital_available=1000.0,
            equity=1000.0,
        )

        self.assertEqual(result.state, PositionState.FLAT)
        self.assertEqual(result.trade_record.exit_reason, "structural_stop")
        self.assertEqual(len(engine.trade_records), 1)
        self.assertEqual(result.trade_record.exit_price, 7.9)
        self.assertAlmostEqual(result.trade_record.net_pnl, -216.0)

    def test_structural_stop_is_derived_from_or_for_call_and_not_caller_boolean(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        # market bar low crosses the stored OR-low stop even though compatibility flag is false
        bars = list(market_bars(session_date=SESSION_DATE))
        bars[-1] = type(bars[-1])(bars[-1].timestamp, bars[-1].open, bars[-1].high, 99.0, bars[-1].close, bars[-1].volume)
        result = engine.process(paper_event(timestamp(9, 40), price=8.0, bars=bars), capital_available=1000.0, equity=1000.0)
        self.assertEqual(result.trade_record.exit_reason, "structural_stop")

    def test_stop_gap_uses_observed_option_price_not_synthetic_stop_price(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        bars = list(market_bars(session_date=SESSION_DATE))
        bars[-1] = type(bars[-1])(bars[-1].timestamp, bars[-1].open, bars[-1].high, 99.0, bars[-1].close, bars[-1].volume)
        result = engine.process(paper_event(timestamp(9, 40), price=4.0, bars=bars), capital_available=1000.0, equity=1000.0)
        self.assertEqual(result.trade_record.exit_reason, "structural_stop")
        self.assertEqual(result.trade_record.exit_price, 3.9)

    def test_simultaneous_structural_and_emergency_exit_uses_declared_precedence(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        bars = list(market_bars(session_date=SESSION_DATE))
        bars[-1] = type(bars[-1])(bars[-1].timestamp, bars[-1].open, bars[-1].high, 99.0, bars[-1].close, bars[-1].volume)
        result = engine.process(paper_event(timestamp(9, 40), price=7.0, bars=bars), capital_available=1000.0, equity=1000.0)
        self.assertEqual(result.trade_record.exit_reason, "structural_stop")

    def test_future_exit_option_price_is_rejected(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        future_price = option_price_snapshot(timestamp(9, 41), price=8.0)
        event = paper_event(timestamp(9, 40), price=8.0, structural_stop=True)
        event = type(event)(**{**event.__dict__, "current_option_price": future_price})
        result = engine.process(event, capital_available=1000.0, equity=1000.0)
        self.assertEqual(result.state, PositionState.OPEN)
        self.assertEqual(result.reasons, ("exit_option_price_not_causal",))

    def test_forced_eod_exit_is_deterministic(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        result = engine.process(
            paper_event(timestamp(15, 30), price=11.0),
            capital_available=1000.0,
            equity=1000.0,
        )

        self.assertEqual(result.state, PositionState.FLAT)
        self.assertEqual(result.trade_record.exit_reason, "forced_eod")

    def test_risk_limit_blocks_entry(self) -> None:
        engine = paper_engine()
        engine.daily_pnl = -100.0
        result = engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)

        self.assertEqual(result.state, PositionState.FLAT)
        self.assertEqual(result.risk_decision.reasons, ("daily_loss_limit_reached",))

    def test_missing_option_delta_fails_closed_for_structural_risk(self) -> None:
        engine = paper_engine()
        candidates = option_candidates()
        candidates = [type(c)(**{**c.__dict__, "delta": None}) for c in candidates]
        event = paper_event(timestamp(9, 35))
        event = type(event)(**{**event.__dict__, "option_candidates": tuple(candidates)})
        result = engine.process(event, capital_available=1000.0, equity=1000.0)
        self.assertEqual(result.state, PositionState.FLAT)
        self.assertEqual(result.reasons, ("option_delta_missing_for_risk",))

    def test_missing_market_data_produces_no_trade(self) -> None:
        engine = paper_engine()
        result = engine.process(
            paper_event(timestamp(9, 35), volume=False), capital_available=1000.0, equity=1000.0
        )

        self.assertEqual(result.state, PositionState.FLAT)
        self.assertEqual(result.signal.signal_state.value, "NO_TRADE")
        self.assertIsNone(result.risk_decision)

    def test_no_trade_mode_never_enters(self) -> None:
        engine = paper_engine()
        result = engine.process(
            paper_event(timestamp(9, 35), mode=Mode.NO_TRADE), capital_available=1000.0, equity=1000.0
        )

        self.assertEqual(result.state, PositionState.FLAT)
        self.assertIsNone(engine.position)

    def test_event_at_entry_timestamp_cannot_exit_position(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        result = engine.process(
            paper_event(timestamp(9, 35), price=8.0, structural_stop=True),
            capital_available=1000.0,
            equity=1000.0,
        )

        self.assertEqual(result.state, PositionState.OPEN)
        self.assertEqual(result.reasons, ("event_not_after_entry",))

    def test_time_exit_precedes_forced_eod_when_configured_first(self) -> None:
        engine = paper_engine(time_exit_after=timedelta(minutes=5))
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        result = engine.process(
            paper_event(timestamp(9, 40), price=11.0), capital_available=1000.0, equity=1000.0
        )

        self.assertEqual(result.trade_record.exit_reason, "time_exit")


if __name__ == "__main__":
    unittest.main()
class RiskControlBehaviorTests(unittest.TestCase):
    def test_emergency_premium_loss_exits_on_selected_contract(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        result = engine.process(paper_event(timestamp(9, 40), price=6.9), capital_available=1000.0, equity=1000.0)
        self.assertEqual(result.state, PositionState.FLAT)
        self.assertEqual(result.trade_record.exit_reason, "emergency_premium_loss")

    def test_zero_emergency_premium_loss_disables_emergency_exit(self) -> None:
        cfg = paper_config()
        rp = cfg.risk_parameters
        disabled = RiskParameters(
            risk_fraction=rp.risk_fraction,
            daily_loss_limit=rp.daily_loss_limit,
            max_trades_per_day=rp.max_trades_per_day,
            consecutive_loss_limit=rp.consecutive_loss_limit,
            emergency_premium_loss=0.0,
            structural_stop_rule_version=rp.structural_stop_rule_version,
            time_exit_rule_version=rp.time_exit_rule_version,
            forced_eod_rule_version=rp.forced_eod_rule_version,
        )
        engine = PaperTradingEngine(
            StrategyEngine(config(), {SESSION_DATE: (EXPIRY,)}),
            PaperTradingConfig(
                risk_parameters=disabled,
                entry_slippage_per_unit=cfg.entry_slippage_per_unit,
                exit_slippage_per_unit=cfg.exit_slippage_per_unit,
                entry_charges_per_lot=cfg.entry_charges_per_lot,
                exit_charges_per_lot=cfg.exit_charges_per_lot,
                forced_eod_time=cfg.forced_eod_time,
                time_exit_after=None,
                exit_precedence=cfg.exit_precedence,
            ),
        )
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        result = engine.process(paper_event(timestamp(9, 40), price=6.9), capital_available=1000.0, equity=1000.0)
        self.assertEqual(result.state, PositionState.OPEN)

    def test_winning_exit_resets_consecutive_losses(self) -> None:
        engine = paper_engine()
        engine.process(paper_event(timestamp(9, 35)), capital_available=1000.0, equity=1000.0)
        result = engine.process(paper_event(timestamp(9, 40), price=20.0), capital_available=1000.0, equity=1000.0)
        # No exit condition at 9:40, so force the deterministic EOD exit with a profit.
        result = engine.process(paper_event(timestamp(15, 30), price=20.0), capital_available=1000.0, equity=1000.0)
        self.assertEqual(result.trade_record.exit_reason, "forced_eod")
        self.assertEqual(engine.consecutive_losses, 0)

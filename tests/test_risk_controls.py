from __future__ import annotations

import unittest

from risk import DerivedStopRisk, PaperRiskEngine, RiskParameters, RiskRequest
from strategy import Direction, Mode, ScoreComponents, Signal, SignalState


class RiskControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PaperRiskEngine()
        self.signal = Signal(
            timestamp=__import__("datetime").datetime(2026, 9, 16, 9, 35),
            mode=Mode.DAILY,
            direction=Direction.CALL,
            signal_state=SignalState.BUY_CALL,
            or_high=100.0, or_low=90.0, or_width=10.0, atr=5.0, or_atr=2.0,
            vwap=100.0, zh_score=80.0,
            score_components=ScoreComponents(1,1,1,1,1,1,1),
            candidate_contracts=(), selected_contract=None, rejection_reasons=(),
            feature_config_version="TEST",
        )

    def params(self, **overrides):
        values = dict(
            risk_fraction=0.1,
            daily_loss_limit=100.0,
            max_trades_per_day=3,
            consecutive_loss_limit=2,
            emergency_premium_loss=3.0,
            structural_stop_rule_version="TEST",
            time_exit_rule_version="TEST",
            forced_eod_rule_version="TEST",
        )
        values.update(overrides)
        return RiskParameters(**values)

    def request(self, **overrides):
        values = dict(
            premium=10.0,
            lot_size=10,
            charges_per_lot=1.0,
            slippage_per_unit=0.1,
            derived_stop_risk=DerivedStopRisk(2.0, "DELTA_GAMMA_STRUCTURAL_STOP_V1"),
            capital_available=1000.0,
            equity=1000.0,
            daily_pnl=0.0,
            trades_today=0,
            consecutive_losses=0,
        )
        values.update(overrides)
        return RiskRequest(**values)

    def test_consecutive_loss_limit_blocks_at_threshold(self):
        result = self.engine.assess(self.signal, self.params(), self.request(consecutive_losses=2))
        self.assertFalse(result.approved)
        self.assertEqual(result.reasons, ("consecutive_loss_limit_reached",))

    def test_consecutive_loss_limit_does_not_block_below_threshold(self):
        result = self.engine.assess(self.signal, self.params(), self.request(consecutive_losses=1))
        self.assertTrue(result.approved)

    def test_zero_consecutive_loss_limit_disables_guard(self):
        result = self.engine.assess(
            self.signal,
            self.params(consecutive_loss_limit=0),
            self.request(consecutive_losses=99),
        )
        self.assertTrue(result.approved)

    def test_daily_loss_exact_boundary_blocks(self):
        result = self.engine.assess(self.signal, self.params(), self.request(daily_pnl=-100.0))
        self.assertFalse(result.approved)
        self.assertEqual(result.reasons, ("daily_loss_limit_reached",))

    def test_daily_loss_just_inside_boundary_allows(self):
        result = self.engine.assess(self.signal, self.params(), self.request(daily_pnl=-99.99))
        self.assertTrue(result.approved)

    def test_max_trades_exact_boundary_blocks(self):
        result = self.engine.assess(self.signal, self.params(), self.request(trades_today=3))
        self.assertFalse(result.approved)
        self.assertEqual(result.reasons, ("maximum_trades_reached",))

    def test_negative_consecutive_loss_configuration_rejected(self):
        result = self.engine.assess(self.signal, self.params(consecutive_loss_limit=-1), self.request())
        self.assertFalse(result.approved)
        self.assertEqual(result.reasons, ("risk_limit_configuration_invalid",))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from dataclasses import fields

from execution import ExecutionInterface, OrderIntent, build_order_intent
from risk import RiskDecision, RiskEngine, RiskParameters, RiskRequest


class InterfaceTests(unittest.TestCase):
    def test_risk_interface_has_required_controls_without_defaults(self) -> None:
        names = {field.name for field in fields(RiskParameters)}
        self.assertEqual(
            names,
            {
                "risk_fraction",
                "daily_loss_limit",
                "max_trades_per_day",
                "consecutive_loss_limit",
                "emergency_premium_loss",
                "structural_stop_rule_version",
                "time_exit_rule_version",
                "forced_eod_rule_version",
                "stop_risk_model_version",
                "risk_config_version",
            },
        )
        self.assertIsNotNone(RiskEngine)
        self.assertIsNotNone(RiskRequest)
        self.assertIsNotNone(RiskDecision)

    def test_execution_boundary_is_order_intent_only(self) -> None:
        self.assertIsNotNone(ExecutionInterface)
        self.assertEqual([field.name for field in fields(OrderIntent)], [
            "timestamp", "direction", "contract", "quantity", "reason"
        ])
        self.assertTrue(callable(build_order_intent))


if __name__ == "__main__":
    unittest.main()
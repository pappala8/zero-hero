"""Deterministic fail-closed risk sizing for the paper-trading engine."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite

from strategy.models import Signal, SignalState

from .interfaces import RiskDecision, RiskParameters, RiskRequest


@dataclass(frozen=True)
class PaperRiskEngine:
    """Apply configured risk gates without placing or simulating orders."""

    def assess(
        self, signal: Signal, parameters: RiskParameters, request: RiskRequest
    ) -> RiskDecision:
        if signal.signal_state not in {SignalState.BUY_CALL, SignalState.BUY_PUT}:
            return RiskDecision(False, None, None, ("signal_not_buyable",))
        required = {
            "risk_fraction": parameters.risk_fraction,
            "daily_loss_limit": parameters.daily_loss_limit,
            "max_trades_per_day": parameters.max_trades_per_day,
            "consecutive_loss_limit": parameters.consecutive_loss_limit,
            "premium": request.premium,
            "lot_size": request.lot_size,
            "charges_per_lot": request.charges_per_lot,
            "slippage_per_unit": request.slippage_per_unit,
            "derived_stop_risk": request.derived_stop_risk,
            "capital_available": request.capital_available,
            "equity": request.equity,
            "daily_pnl": request.daily_pnl,
            "trades_today": request.trades_today,
            "consecutive_losses": request.consecutive_losses,
        }
        if any(value is None for value in required.values()):
            return RiskDecision(False, None, None, ("required_risk_input_missing",))
        if parameters.risk_fraction <= 0:
            return RiskDecision(False, None, None, ("risk_fraction_invalid",))
        if parameters.daily_loss_limit <= 0 or parameters.max_trades_per_day <= 0 or parameters.consecutive_loss_limit < 0:
            return RiskDecision(False, None, None, ("risk_limit_configuration_invalid",))
        if request.equity <= 0 or request.capital_available <= 0:
            return RiskDecision(False, None, None, ("capital_or_equity_invalid",))
        if request.premium <= 0 or request.lot_size <= 0 or request.derived_stop_risk.value <= 0:
            return RiskDecision(False, None, None, ("position_input_invalid",))
        if request.charges_per_lot < 0 or request.slippage_per_unit < 0:
            return RiskDecision(False, None, None, ("cost_input_invalid",))
        if request.derived_stop_risk.model_version != parameters.stop_risk_model_version:
            return RiskDecision(False, None, None, ("stop_risk_model_version_mismatch",))
        if request.daily_pnl <= -parameters.daily_loss_limit:
            return RiskDecision(False, None, None, ("daily_loss_limit_reached",))
        if request.trades_today >= parameters.max_trades_per_day:
            return RiskDecision(False, None, None, ("maximum_trades_reached",))
        # Zero explicitly disables consecutive-loss protection. Positive values
        # are hard stop thresholds.
        if parameters.consecutive_loss_limit > 0 and request.consecutive_losses >= parameters.consecutive_loss_limit:
            return RiskDecision(False, None, None, ("consecutive_loss_limit_reached",))

        values = (
            request.premium,
            request.lot_size,
            request.charges_per_lot,
            request.slippage_per_unit,
            request.derived_stop_risk.value,
            request.capital_available,
            request.equity,
        )
        if not all(isfinite(value) for value in values):
            return RiskDecision(False, None, None, ("position_input_non_finite",))
        risk_budget = request.equity * parameters.risk_fraction
        risk_per_lot = (
            request.derived_stop_risk.value * request.lot_size
            + request.charges_per_lot
            + request.slippage_per_unit * request.lot_size
        )
        capital_per_lot = (
            request.premium * request.lot_size
            + request.charges_per_lot
            + request.slippage_per_unit * request.lot_size
        )
        if risk_per_lot <= 0 or capital_per_lot <= 0:
            return RiskDecision(False, None, None, ("risk_or_capital_cost_invalid",))
        risk_lots = floor(risk_budget / risk_per_lot)
        capital_lots = floor(request.capital_available / capital_per_lot)
        lots = min(risk_lots, capital_lots)
        if lots < 1:
            return RiskDecision(False, None, None, ("insufficient_risk_or_capital_capacity",))
        return RiskDecision(True, lots, lots * request.lot_size, ())
"""Broker-free risk handoff contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from strategy.models import Signal


@dataclass(frozen=True)
class RiskParameters:
    risk_fraction: Optional[float]
    daily_loss_limit: Optional[float]
    max_trades_per_day: Optional[int]
    consecutive_loss_limit: Optional[int]
    emergency_premium_loss: Optional[float]
    structural_stop_rule_version: Optional[str]
    time_exit_rule_version: Optional[str]
    forced_eod_rule_version: Optional[str]
    stop_risk_model_version: str = "DELTA_GAMMA_STRUCTURAL_STOP_V1"
    risk_config_version: str = "ZH_RISK_V0.3"


@dataclass(frozen=True)
class DerivedStopRisk:
    """Risk per option unit derived by the paper engine from causal structure."""

    value: float
    model_version: str

    def __post_init__(self) -> None:
        if self.value <= 0 or not __import__("math").isfinite(self.value):
            raise ValueError("derived stop risk must be finite and positive")
        if not self.model_version:
            raise ValueError("derived stop risk model version is required")


@dataclass(frozen=True)
class RiskRequest:
    premium: Optional[float]
    lot_size: Optional[int]
    charges_per_lot: Optional[float]
    slippage_per_unit: Optional[float]
    derived_stop_risk: Optional[DerivedStopRisk]
    capital_available: Optional[float]
    equity: Optional[float] = None
    daily_pnl: Optional[float] = None
    trades_today: Optional[int] = None
    consecutive_losses: Optional[int] = None


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    lots: Optional[int]
    quantity: Optional[int]
    reasons: tuple[str, ...]


class RiskEngine(Protocol):
    def assess(
        self, signal: Signal, parameters: RiskParameters, request: RiskRequest
    ) -> RiskDecision:
        """Return a fail-closed risk decision without placing an order."""
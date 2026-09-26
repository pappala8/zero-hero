"""Broker-free paper-trading state machine around the strategy core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from math import isfinite
from typing import Mapping, Optional
from zoneinfo import ZoneInfo

from features import Bar
from risk import DerivedStopRisk, PaperRiskEngine, RiskDecision, RiskParameters, RiskRequest
from strategy import Mode, OptionCandidate, OptionPriceSnapshot, Signal, SignalState, StrategyEngine, UnderlyingReference

from .interfaces import OrderIntent, build_order_intent

IST = ZoneInfo("Asia/Kolkata")


class PositionState(str, Enum):
    FLAT = "FLAT"
    OPEN = "OPEN"


@dataclass(frozen=True)
class PaperTradingConfig:
    risk_parameters: RiskParameters
    entry_slippage_per_unit: Optional[float]
    exit_slippage_per_unit: Optional[float]
    entry_charges_per_lot: Optional[float]
    exit_charges_per_lot: Optional[float]
    forced_eod_time: Optional[time]
    time_exit_after: Optional[timedelta]
    exit_precedence: tuple[str, ...]
    fill_model_version: str = "OBSERVED_PRICE_PLUS_EXPLICIT_SLIPPAGE_V1"
    paper_config_version: str = "ZH_PAPER_V0.5"

    def __post_init__(self) -> None:
        allowed = {"structural_stop", "emergency_premium_loss", "time_exit", "forced_eod"}
        if set(self.exit_precedence) != allowed or len(self.exit_precedence) != len(allowed):
            raise ValueError("exit_precedence must explicitly order every supported exit condition")
        numeric = (
            self.entry_slippage_per_unit,
            self.exit_slippage_per_unit,
            self.entry_charges_per_lot,
            self.exit_charges_per_lot,
        )
        if any(value is None for value in numeric):
            raise ValueError("paper fill and charge inputs must be explicit")
        if any(not isfinite(value) or value < 0 for value in numeric):
            raise ValueError("paper fill and charge inputs must be finite and non-negative")
        if self.forced_eod_time is None:
            raise ValueError("forced_eod_time must be explicit")
        if self.time_exit_after is not None and self.time_exit_after <= timedelta(0):
            raise ValueError("time_exit_after must be positive when configured")


@dataclass(frozen=True)
class PaperBarEvent:
    timestamp: datetime
    session_date: date
    mode: Mode
    underlying_bars: tuple[Bar, ...]
    volume_bars: tuple[Bar, ...]
    score_components: Mapping[str, Optional[float]]
    option_candidates: tuple[OptionCandidate, ...]
    selected_expiry: date
    underlying_reference: Optional[UnderlyingReference]
    current_option_price: Optional[OptionPriceSnapshot]
    lot_size: Optional[int]
    # Deprecated compatibility field. It is deliberately ignored by the engine.
    stop_risk_per_unit: Optional[float] = None
    structural_stop_triggered: bool = False


@dataclass(frozen=True)
class PaperPosition:
    contract: OptionCandidate
    direction: str
    entry_timestamp: datetime
    entry_price: float
    quantity: int
    lots: int
    entry_charges: float
    stop_risk_per_unit: float
    structural_stop_level: float


@dataclass(frozen=True)
class TradeRecord:
    contract: OptionCandidate
    direction: str
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: float
    exit_price: float
    quantity: int
    lots: int
    gross_pnl: float
    transaction_costs: float
    net_pnl: float
    exit_reason: str


@dataclass(frozen=True)
class PaperEventResult:
    state: PositionState
    signal: Signal
    risk_decision: Optional[RiskDecision]
    order_intent: Optional[OrderIntent]
    trade_record: Optional[TradeRecord]
    reasons: tuple[str, ...]


@dataclass
class PaperTradingEngine:
    strategy_engine: StrategyEngine
    config: PaperTradingConfig
    risk_engine: PaperRiskEngine = field(default_factory=PaperRiskEngine)
    position: Optional[PaperPosition] = None
    trade_records: list[TradeRecord] = field(default_factory=list)
    daily_pnl: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0

    @property
    def state(self) -> PositionState:
        return PositionState.OPEN if self.position is not None else PositionState.FLAT

    def reset_daily_state(self) -> None:
        """Reset session counters only when no position crosses the boundary."""

        if self.position is not None:
            raise RuntimeError("cannot reset daily state with an open position")
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0

    def process(self, event: PaperBarEvent, *, capital_available: Optional[float], equity: Optional[float]) -> PaperEventResult:
        signal = self.strategy_engine.evaluate(
            session_date=event.session_date,
            event_timestamp=event.timestamp,
            mode=event.mode,
            underlying_bars=event.underlying_bars,
            volume_bars=event.volume_bars,
            score_components=event.score_components,
            option_candidates=event.option_candidates,
            selected_expiry=event.selected_expiry,
            underlying_reference=event.underlying_reference,
        )
        if self.position is not None:
            return self._process_open_position(event, signal)
        if signal.signal_state not in {SignalState.BUY_CALL, SignalState.BUY_PUT}:
            return PaperEventResult(self.state, signal, None, None, None, signal.rejection_reasons)
        if event.current_option_price is None or event.lot_size is None:
            return PaperEventResult(self.state, signal, None, None, None, ("entry_market_input_missing",))
        if (
            event.current_option_price.timestamp > event.timestamp
            or event.current_option_price.available_at > event.timestamp
        ):
            return PaperEventResult(self.state, signal, None, None, None, ("entry_option_price_not_causal",))
        selected = signal.selected_contract
        parameters = self.config.risk_parameters
        if selected is None:
            return PaperEventResult(self.state, signal, None, None, None, ("selected_contract_missing",))
        if event.current_option_price.contract_identity != selected.contract_identity:
            return PaperEventResult(self.state, signal, None, None, None, ("option_price_contract_mismatch",))
        if event.underlying_reference is None:
            return PaperEventResult(self.state, signal, None, None, None, ("entry_underlying_reference_missing",))
        if event.underlying_reference.timestamp > event.timestamp or event.underlying_reference.available_at > event.timestamp:
            return PaperEventResult(self.state, signal, None, None, None, ("entry_underlying_reference_not_causal",))
        if selected.delta is None or not isfinite(selected.delta):
            return PaperEventResult(self.state, signal, None, None, None, ("option_delta_missing_for_risk",))
        if selected.gamma is not None and not isfinite(selected.gamma):
            return PaperEventResult(self.state, signal, None, None, None, ("option_gamma_invalid_for_risk",))
        if signal.direction is None or signal.or_high is None or signal.or_low is None:
            return PaperEventResult(self.state, signal, None, None, None, ("structural_stop_reference_missing",))
        structural_stop_level = signal.or_low if signal.direction.value == "CALL" else signal.or_high
        underlying_distance = abs(event.underlying_reference.price - structural_stop_level)
        if not isfinite(underlying_distance) or underlying_distance <= 0:
            return PaperEventResult(self.state, signal, None, None, None, ("structural_stop_distance_invalid",))
        gamma_term = 0.5 * abs(selected.gamma or 0.0) * underlying_distance * underlying_distance
        delta_term = abs(selected.delta) * underlying_distance
        stop_risk_per_unit = min(event.current_option_price.price, delta_term + gamma_term)
        if not isfinite(stop_risk_per_unit) or stop_risk_per_unit <= 0:
            return PaperEventResult(self.state, signal, None, None, None, ("derived_stop_risk_invalid",))
        request = RiskRequest(
            premium=event.current_option_price.price,
            lot_size=event.lot_size,
            charges_per_lot=self.config.entry_charges_per_lot,
            slippage_per_unit=self.config.entry_slippage_per_unit,
            derived_stop_risk=DerivedStopRisk(
                value=stop_risk_per_unit,
                model_version=parameters.stop_risk_model_version,
            ),
            capital_available=capital_available,
            equity=equity,
            daily_pnl=self.daily_pnl,
            trades_today=self.trades_today,
            consecutive_losses=self.consecutive_losses,
        )
        decision = self.risk_engine.assess(signal, parameters, request)
        if not decision.approved or decision.quantity is None or decision.lots is None:
            return PaperEventResult(self.state, signal, decision, None, None, decision.reasons)
        entry_price = event.current_option_price.price + self.config.entry_slippage_per_unit
        intent = build_order_intent(signal, decision.quantity)
        if intent is None or not isfinite(entry_price) or entry_price <= 0:
            return PaperEventResult(self.state, signal, decision, None, None, ("entry_fill_invalid",))
        self.position = PaperPosition(
            contract=selected,
            direction=signal.direction.value,
            entry_timestamp=event.timestamp,
            entry_price=entry_price,
            quantity=decision.quantity,
            lots=decision.lots,
            entry_charges=self.config.entry_charges_per_lot * decision.lots,
            stop_risk_per_unit=stop_risk_per_unit,
            structural_stop_level=structural_stop_level,
        )
        self.trades_today += 1
        return PaperEventResult(self.state, signal, decision, intent, None, ())

    def _process_open_position(self, event: PaperBarEvent, signal: Signal) -> PaperEventResult:
        position = self.position
        if position is None:
            raise RuntimeError("open-position handler requires a position")
        if event.timestamp <= position.entry_timestamp:
            return PaperEventResult(self.state, signal, None, None, None, ("event_not_after_entry",))
        observed_bars = tuple(
            bar for bar in event.underlying_bars
            if bar.timestamp <= event.timestamp
        )
        if not observed_bars:
            return PaperEventResult(self.state, signal, None, None, None, ("underlying_exit_input_missing",))
        latest_bar = max(observed_bars, key=lambda bar: bar.timestamp)
        if position.direction == "CALL":
            structural_stop_observed = latest_bar.low <= position.structural_stop_level
        elif position.direction == "PUT":
            structural_stop_observed = latest_bar.high >= position.structural_stop_level
        else:
            return PaperEventResult(self.state, signal, None, None, None, ("position_direction_invalid",))
        premium_loss_threshold = self.config.risk_parameters.emergency_premium_loss
        if event.current_option_price is not None:
            if event.current_option_price.timestamp > event.timestamp or event.current_option_price.available_at > event.timestamp:
                return PaperEventResult(self.state, signal, None, None, None, ("exit_option_price_not_causal",))
            if event.current_option_price.contract_identity != position.contract.contract_identity:
                return PaperEventResult(self.state, signal, None, None, None, ("option_price_contract_mismatch",))
        option_price_causal = event.current_option_price is not None
        premium_loss_observed = (
            premium_loss_threshold is not None
            and premium_loss_threshold > 0
            and option_price_causal
            and position.entry_price - event.current_option_price.price >= premium_loss_threshold
        )
        conditions = {
            "structural_stop": structural_stop_observed,
            "emergency_premium_loss": premium_loss_observed,
            "time_exit": self.config.time_exit_after is not None and event.timestamp - position.entry_timestamp >= self.config.time_exit_after,
            "forced_eod": event.timestamp.astimezone(IST).time() >= self.config.forced_eod_time,
        }
        exit_reason = next((reason for reason in self.config.exit_precedence if conditions[reason]), None)
        if exit_reason is None:
            return PaperEventResult(self.state, signal, None, None, None, ())
        if event.current_option_price is None:
            return PaperEventResult(self.state, signal, None, None, None, ("exit_market_input_missing",))
        if (
            event.current_option_price.timestamp > event.timestamp
            or event.current_option_price.available_at > event.timestamp
        ):
            return PaperEventResult(self.state, signal, None, None, None, ("exit_option_price_not_causal",))
        if event.current_option_price.contract_identity != position.contract.contract_identity:
            return PaperEventResult(self.state, signal, None, None, None, ("option_price_contract_mismatch",))
        exit_price = event.current_option_price.price - self.config.exit_slippage_per_unit
        if not isfinite(exit_price) or exit_price <= 0:
            return PaperEventResult(self.state, signal, None, None, None, ("exit_fill_invalid",))
        exit_charges = self.config.exit_charges_per_lot * position.lots
        gross_pnl = (exit_price - position.entry_price) * position.quantity
        transaction_costs = position.entry_charges + exit_charges
        net_pnl = gross_pnl - transaction_costs
        record = TradeRecord(
            contract=position.contract,
            direction=position.direction,
            entry_timestamp=position.entry_timestamp,
            exit_timestamp=event.timestamp,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            lots=position.lots,
            gross_pnl=gross_pnl,
            transaction_costs=transaction_costs,
            net_pnl=net_pnl,
            exit_reason=exit_reason,
        )
        self.trade_records.append(record)
        self.daily_pnl += net_pnl
        self.consecutive_losses = self.consecutive_losses + 1 if net_pnl < 0 else 0
        self.position = None
        return PaperEventResult(self.state, signal, None, None, record, ())
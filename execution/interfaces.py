"""Broker-free order intent boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from strategy.models import Direction, OptionCandidate, Signal, SignalState


@dataclass(frozen=True)
class OrderIntent:
    timestamp: datetime
    direction: Direction
    contract: OptionCandidate
    quantity: int
    reason: str = "strategy_signal"


def build_order_intent(signal: Signal, quantity: int) -> OrderIntent | None:
    """Convert an approved signal into an intent without submitting it anywhere."""

    if signal.signal_state not in {SignalState.BUY_CALL, SignalState.BUY_PUT}:
        return None
    if signal.selected_contract is None or quantity <= 0:
        return None
    return OrderIntent(signal.timestamp, signal.direction, signal.selected_contract, quantity)


class ExecutionInterface(Protocol):
    def submit_intent(self, intent: OrderIntent) -> None:
        """Accept an order intent; implementations must define paper/live boundaries."""
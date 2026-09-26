"""Provider interface; deliberately contains no order-placement capability."""
from __future__ import annotations
from enum import Enum
from typing import Iterable, Protocol
from .models import RealtimeRawEvent

class ProviderConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    STREAMING = "STREAMING"
    RECONNECTING = "RECONNECTING"
    AUTH_FAILED = "AUTH_FAILED"
    STOPPED = "STOPPED"

class MarketDataProvider(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def subscribe(self, instrument_tokens: Iterable[int]) -> None: ...
    def unsubscribe(self, instrument_tokens: Iterable[int]) -> None: ...
    def events(self) -> Iterable[RealtimeRawEvent]: ...

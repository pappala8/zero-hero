"""Pure Zero Hero V0.1 signal engine with no broker dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from math import isfinite
from typing import Collection, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from features import Bar, calculate_atr, calculate_opening_range, calculate_or_atr, calculate_vwap

from .models import Direction, Mode, OptionCandidate, ScoreComponents, Signal, SignalState, UnderlyingReference


@dataclass(frozen=True)
class StrategyConfig:
    breakout_buffer: float
    atr_lookback: int
    atr_method: str
    score_threshold: Optional[float]
    vwap_volume_source: Optional[str]
    require_vwap_alignment: bool
    strike_step: float
    strike_mapping_version: str
    option_required_fields: tuple[str, ...]
    session_start: time
    session_end: time
    selection_priority: tuple[int, ...]
    confirmation_window_bars: int = 1
    confirmation_bars_required: int = 1
    feature_config_version: str = "ZH_FEATURES_V0.2"

    def __post_init__(self) -> None:
        if not isfinite(self.breakout_buffer) or self.breakout_buffer < 0:
            raise ValueError("breakout_buffer must be finite and non-negative")
        if self.atr_lookback <= 0:
            raise ValueError("atr_lookback must be positive")
        if not isfinite(self.strike_step) or self.strike_step <= 0:
            raise ValueError("strike_step must be finite and positive")
        if not self.strike_mapping_version:
            raise ValueError("strike_mapping_version is required")
        if not self.option_required_fields:
            raise ValueError("option_required_fields must be explicit")
        if self.score_threshold is not None and not 0 <= self.score_threshold <= 100:
            raise ValueError("score_threshold must be between 0 and 100")
        if self.session_start >= self.session_end:
            raise ValueError("session_start must be before session_end")
        if sorted(self.selection_priority) != [0, 1, 2]:
            raise ValueError("selection_priority must contain ATM, 1 OTM, and 2 OTM ranks")
        if self.confirmation_window_bars <= 0:
            raise ValueError("confirmation_window_bars must be positive")
        if self.confirmation_bars_required <= 0:
            raise ValueError("confirmation_bars_required must be positive")
        if self.confirmation_bars_required > self.confirmation_window_bars:
            raise ValueError("confirmation_bars_required cannot exceed confirmation_window_bars")


@dataclass(frozen=True)
class OptionSelection:
    candidates: tuple[OptionCandidate, ...]
    eligible: tuple[OptionCandidate, ...]
    rejection_reasons: tuple[str, ...]


def _round_to_step(value: float, step: float) -> float:
    return round(value / step) * step


def _candidate_rank(strike: float, atm: float, direction: Direction, step: float) -> Optional[int]:
    offset = (strike - atm) / step
    expected = round(offset)
    if abs(offset - expected) > 1e-9 or expected not in {-2, -1, 0, 1, 2}:
        return None
    if direction is Direction.CALL and expected < 0:
        return None
    if direction is Direction.PUT and expected > 0:
        return None
    return abs(expected)


def select_option_candidates(
    candidates: Iterable[OptionCandidate],
    underlying_reference: float,
    direction: Direction,
    signal_timestamp: datetime,
    selected_expiry: date,
    strike_step: float,
    required_fields: tuple[str, ...],
    selection_priority: tuple[int, ...],
) -> OptionSelection:
    """Select only timestamp-causal ATM/OTM candidates for one expiry."""

    atm = _round_to_step(underlying_reference, strike_step)
    latest_by_contract: dict[tuple[str, date, float, str], OptionCandidate] = {}
    for candidate in candidates:
        if candidate.timestamp > signal_timestamp or candidate.available_at > signal_timestamp:
            continue
        if candidate.expiry != selected_expiry or candidate.underlying != "NIFTY 50":
            continue
        expected_right = "CE" if direction is Direction.CALL else "PE"
        if candidate.right != expected_right:
            continue
        rank = _candidate_rank(candidate.strike, atm, direction, strike_step)
        if rank is None:
            continue
        key = candidate.contract_identity
        current = latest_by_contract.get(key)
        if current is None or candidate.timestamp > current.timestamp:
            latest_by_contract[key] = candidate

    matching = []
    for candidate in latest_by_contract.values():
        rank = _candidate_rank(candidate.strike, atm, direction, strike_step)
        if rank is not None:
            matching.append((rank, candidate))

    matching.sort(key=lambda item: (selection_priority.index(item[0]), item[0], item[1].strike))
    visible = tuple(candidate for _, candidate in matching)
    eligible = tuple(candidate for _, candidate in matching if candidate.has_required_fields(required_fields))
    reasons: list[str] = []
    if not visible:
        reasons.append("option_candidate_unavailable_at_signal_timestamp")
    elif not eligible:
        reasons.append("option_quality_evidence_missing")
    return OptionSelection(visible, eligible, tuple(reasons))


class StrategyEngine:
    def __init__(self, config: StrategyConfig, expiry_calendar: Mapping[date, Collection[date]]) -> None:
        self.config = config
        self.expiry_calendar = expiry_calendar

    def evaluate(
        self,
        *,
        session_date: date,
        event_timestamp: datetime,
        mode: Mode,
        underlying_bars: Iterable[Bar],
        score_components: Mapping[str, Optional[float]],
        option_candidates: Iterable[OptionCandidate],
        selected_expiry: date,
        underlying_reference: Optional[UnderlyingReference] = None,
        volume_bars: Optional[Iterable[Bar]] = None,
    ) -> Signal:
        components = ScoreComponents.from_mapping(score_components)
        empty = dict(
            timestamp=event_timestamp,
            mode=mode,
            direction=None,
            signal_state=SignalState.NO_TRADE,
            or_high=None,
            or_low=None,
            or_width=None,
            atr=None,
            or_atr=None,
            vwap=None,
            zh_score=None,
            score_components=components,
            candidate_contracts=(),
            selected_contract=None,
            rejection_reasons=(),
            feature_config_version=self.config.feature_config_version,
        )
        if mode is Mode.NO_TRADE:
            empty["rejection_reasons"] = ("no_trade_capital_protection_mode",)
            return Signal(**empty)
        if event_timestamp.tzinfo is None or event_timestamp.utcoffset() is None:
            empty["rejection_reasons"] = ("event_timestamp_timezone_missing",)
            return Signal(**empty)
        event_local = event_timestamp.astimezone(ZoneInfo("Asia/Kolkata"))
        if event_local.date() != session_date or not self.config.session_start <= event_local.time() <= self.config.session_end:
            empty["rejection_reasons"] = ("event_outside_configured_session",)
            return Signal(**empty)

        bars = tuple(underlying_bars)
        opening_range = calculate_opening_range(bars, session_date, event_timestamp)
        if not opening_range.available:
            empty["rejection_reasons"] = (opening_range.reason or "opening_range_unavailable",)
            return Signal(**empty)

        atr = calculate_atr(bars, event_timestamp, self.config.atr_lookback, self.config.atr_method)  # type: ignore[arg-type]
        or_atr = calculate_or_atr(opening_range, atr)
        if not atr.available or not or_atr.available:
            empty.update(
                or_high=opening_range.high,
                or_low=opening_range.low,
                or_width=opening_range.width,
                rejection_reasons=(atr.reason or or_atr.reason or "atr_unavailable",),
            )
            return Signal(**empty)

        vwap = calculate_vwap(
            bars,
            tuple(volume_bars) if volume_bars is not None else (),
            session_date,
            event_timestamp,
            self.config.vwap_volume_source,
            self.config.session_start,
        )
        completed = [bar for bar in bars if bar.timestamp <= event_timestamp]
        completed.sort(key=lambda bar: bar.timestamp)
        if not completed:
            empty["rejection_reasons"] = ("underlying_observation_missing",)
            return Signal(**empty)

        # Confirmation is evaluated only on completed observations after the OR.
        # A breakout must occur within the configured window and remain outside the
        # corresponding OR boundary for the required number of consecutive bars.
        post_or = [bar for bar in completed if bar.timestamp > opening_range.available_at]
        if not post_or:
            empty["rejection_reasons"] = ("confirmation_window_unavailable",)
            return Signal(**empty)
        window = post_or[: self.config.confirmation_window_bars]
        call_threshold = opening_range.high + self.config.breakout_buffer
        put_threshold = opening_range.low - self.config.breakout_buffer
        call_hits = [bar for bar in window if bar.close > call_threshold]
        put_hits = [bar for bar in window if bar.close < put_threshold]
        if call_hits and put_hits:
            empty.update(
                or_high=opening_range.high,
                or_low=opening_range.low,
                or_width=opening_range.width,
                atr=atr.value,
                or_atr=or_atr.value,
                vwap=vwap.value if vwap.available else None,
                rejection_reasons=("contradictory_breakout_directions",),
            )
            return Signal(**empty)

        direction = None
        confirmation_bars = []
        for candidate_direction, threshold, comparator in (
            (Direction.CALL, call_threshold, lambda close: close > threshold),
            (Direction.PUT, put_threshold, lambda close: close < threshold),
        ):
            hits = []
            for bar in window:
                if comparator(bar.close):
                    hits.append(bar)
                    if len(hits) >= self.config.confirmation_bars_required:
                        direction = candidate_direction
                        confirmation_bars = hits
                        break
                else:
                    # A return inside the OR after a directional breakout invalidates
                    # that attempt; later bars may form a new attempt within the window.
                    hits = []
            if direction is not None:
                break

        if direction is None:
            failed = any(
                (bar.close <= opening_range.high if call_hits else False) or
                (bar.close >= opening_range.low if put_hits else False)
                for bar in window[1:]
            )
            reason = "failed_breakout_invalidated" if failed else "breakout_confirmation_not_met"
            empty.update(
                or_high=opening_range.high,
                or_low=opening_range.low,
                or_width=opening_range.width,
                atr=atr.value,
                or_atr=or_atr.value,
                vwap=vwap.value if vwap.available else None,
                rejection_reasons=(reason,),
            )
            return Signal(**empty)

        latest_close = completed[-1].close
        if self.config.require_vwap_alignment and not vwap.available:
            empty["rejection_reasons"] = (vwap.reason or "vwap_unavailable",)
            return Signal(**empty)
        if vwap.available:
            aligned = latest_close > vwap.value if direction is Direction.CALL else latest_close < vwap.value
            if not aligned:
                empty["rejection_reasons"] = ("vwap_direction_not_aligned",)
                return Signal(**empty)

        if not vwap.available:
            components = ScoreComponents(
                components.compression,
                components.breakout,
                None,
                components.volume,
                components.trend,
                components.key_level,
                components.option_quality,
            )
        score = components.score()
        if score is None:
            empty.update(
                direction=direction,
                or_high=opening_range.high,
                or_low=opening_range.low,
                or_width=opening_range.width,
                atr=atr.value,
                or_atr=or_atr.value,
                vwap=vwap.value if vwap.available else None,
                score_components=components,
                rejection_reasons=("required_score_evidence_missing_or_invalid",),
            )
            return Signal(**empty)
        if self.config.score_threshold is None:
            empty.update(
                direction=direction,
                signal_state=SignalState.REVIEW,
                or_high=opening_range.high,
                or_low=opening_range.low,
                or_width=opening_range.width,
                atr=atr.value,
                or_atr=or_atr.value,
                vwap=vwap.value if vwap.available else None,
                zh_score=score,
                score_components=components,
                rejection_reasons=("score_threshold_unconfigured",),
            )
            return Signal(**empty)
        if score < self.config.score_threshold:
            empty.update(
                direction=direction,
                or_high=opening_range.high,
                or_low=opening_range.low,
                or_width=opening_range.width,
                atr=atr.value,
                or_atr=or_atr.value,
                vwap=vwap.value if vwap.available else None,
                zh_score=score,
                score_components=components,
                rejection_reasons=("score_below_configured_threshold",),
            )
            return Signal(**empty)
        if selected_expiry not in self.expiry_calendar.get(session_date, ()):
            empty["rejection_reasons"] = ("expiry_not_in_historical_calendar",)
            return Signal(**empty)
        if underlying_reference is None:
            empty["rejection_reasons"] = ("underlying_reference_missing_or_invalid",)
            return Signal(**empty)
        if underlying_reference.instrument != "NIFTY 50":
            empty["rejection_reasons"] = ("underlying_reference_instrument_mismatch",)
            return Signal(**empty)
        if underlying_reference.available_at > event_timestamp or underlying_reference.timestamp > event_timestamp:
            empty["rejection_reasons"] = ("underlying_reference_not_causal_at_signal",)
            return Signal(**empty)

        selection = select_option_candidates(
            option_candidates,
            underlying_reference.price,
            direction,
            event_timestamp,
            selected_expiry,
            self.config.strike_step,
            self.config.option_required_fields,
            self.config.selection_priority,
        )
        selected = None
        for rank in self.config.selection_priority:
            ranked = [candidate for candidate in selection.eligible if abs(abs(candidate.strike - _round_to_step(underlying_reference.price, self.config.strike_step)) / self.config.strike_step) == rank]
            if ranked:
                selected = ranked[0]
                break
        if selected is None:
            empty.update(
                direction=direction,
                or_high=opening_range.high,
                or_low=opening_range.low,
                or_width=opening_range.width,
                atr=atr.value,
                or_atr=or_atr.value,
                vwap=vwap.value if vwap.available else None,
                zh_score=score,
                score_components=components,
                candidate_contracts=selection.candidates,
                rejection_reasons=selection.rejection_reasons,
            )
            return Signal(**empty)
        return Signal(
            timestamp=event_timestamp,
            mode=mode,
            direction=direction,
            signal_state=SignalState.BUY_CALL if direction is Direction.CALL else SignalState.BUY_PUT,
            or_high=opening_range.high,
            or_low=opening_range.low,
            or_width=opening_range.width,
            atr=atr.value,
            or_atr=or_atr.value,
            vwap=vwap.value if vwap.available else None,
            zh_score=score,
            score_components=components,
            candidate_contracts=selection.candidates,
            selected_contract=selected,
            rejection_reasons=(),
            feature_config_version=self.config.feature_config_version,
        )
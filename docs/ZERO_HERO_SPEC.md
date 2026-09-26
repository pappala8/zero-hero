# Zero Hero System Specification

## Status and Scope

This document defines the initial system contract for Zero Hero (ZH), an automated intraday NIFTY 50 option-buying research and trading system. It is a specification foundation, not an implementation or a statement that the strategy is profitable.

Current implementation status: research-engineering scaffolding is implemented through the strategy/paper-trading and data/QC layers, but the system has not passed the historical-data QC/release gate and is not approved for backtesting, optimization, broker integration, or live deployment. Strategy Engine V0.1 is an implementation of the documented research hypothesis and must remain configurable and uncalibrated until evidence supports any parameter selection. This document governs behavior; implementation does not imply validation or profitability.

## Requirement Classification

- **Fixed requirement:** a boundary or behavior that must be preserved unless explicitly changed through a documented design decision.
- **Research hypothesis:** a testable idea that may be rejected by evidence.
- **Configurable parameter:** a value exposed for experiments rather than embedded as an unexplained rule.
- **Empirical calibration:** a value that must be selected from historical evidence and validation.
- **Future feature:** explicitly deferred capability.

The numerical values in this specification are research ranges or initial concepts unless marked otherwise. They are not optimized production settings.

## Fixed System Boundaries

- Primary underlying: NIFTY 50.
- Strategy type: intraday option buying.
- Initial strategy timeframe: 5-minute.
- 1-minute underlying and option data is preferred for realistic execution simulation.
- Historical research and backtesting are isolated from live execution.
- Backtests and research must never place broker orders or directly call live broker-order functions.
- No-trade is a valid outcome; the system must not force a trade every day.
- All times must be represented with an explicit timezone, using IST for Indian market session logic.
- Every signal must use only information available at its event time.

## Operating Modes

### Daily ZH

A normal intraday mode that may trade when the market, signal, option, and risk gates pass.

### Expiry ZH

A separately evaluated mode for expiry-session behavior. It may have different hypotheses and configuration, but it must not inherit assumptions from Daily ZH without validation.
It must use the historically applicable contract and market expiry calendar for each date and must not rely on a hard-coded weekday assumption.

### No-Trade / Capital Protection

A first-class mode entered when data quality, market conditions, risk limits, or model confidence do not justify a trade. It must be observable and explainable.

## Market Logic Contract

The initial opening range is 09:15-09:30 IST, subject to confirmation against the applicable trading-session calendar. For the initial 5-minute timeframe, it consists exactly of the three completed intervals 09:15-09:20, 09:20-09:25, and 09:25-09:30 IST. Breakout evaluation must not use the final 09:25-09:30 candle before that candle has closed; the opening-range values become available only after the interval is complete.

- `OR_H`: highest high in the opening range.
- `OR_L`: lowest low in the opening range.
- `OR_W`: `OR_H - OR_L`.
- `ATR`: a volatility measure whose method and lookback are research parameters requiring empirical calibration.
- `OR_ATR`: `OR_W / ATR`.

VWAP is a directional filter, but its calculation method, source, session reset behavior, and dependency on the available volume source are research and data decisions, not fixed assumptions. A CALL setup requires an upside structural breakout with confirmation. A PUT setup requires a downside structural breakout with confirmation. Confirmation may include breakout distance, VWAP alignment, volume, trend, key levels, and option quality.

Failed breakouts and poor or choppy conditions must be filtered. The strategy must record which gates passed or failed rather than silently discarding a candidate.

## ZH Score

The initial conceptual score has a maximum of 100:

| Component | Conceptual weight |
|---|---:|
| Compression | 20 |
| Breakout | 20 |
| VWAP | 15 |
| Volume | 15 |
| Trend | 10 |
| Key level | 10 |
| Option quality | 10 |
| **Total** | **100** |

The score threshold is not final. It must be calibrated on historical data using development, out-of-sample, and walk-forward procedures while controlling overfitting. Component definitions, missing-data behavior, and score thresholds must be versioned. Missing or invalid evidence must never receive a favorable or default score. Required missing evidence must fail the relevant gate or produce `REVIEW`/`NO-TRADE`.

## Option Selection Contract

Initially evaluate ATM, 1 OTM, and 2 OTM candidates. This is a research scope, not a claim that farther OTM options are better. Candidate ranking must consider, when reliable:

- delta and available Greeks
- liquidity and volume
- bid-ask spread
- premium
- open interest
- distance from ATM
- expiry and time to expiry

Historical traded option prices are preferred over theoretical repricing when available. Expired contracts must be represented correctly. Contract identity must include underlying, expiry, strike, and CE/PE. Only contracts that were actually tradable for the relevant historical time may be selected. Option candidate selection must use only information available at the signal event timestamp; later prices, later open interest, later Greeks, later contract status, and future market data must not influence selection.

## Risk Contract

Fixed fractional risk per trade is the intended framework. Risk controls must include:

- daily loss limit
- maximum trades per day
- consecutive-loss protection
- emergency premium-loss protection
- structural underlying-based stops
- time-based exits
- forced end-of-day exit

Exact numerical values remain research parameters until validated. Risk sizing must account for premium, lot size, charges, slippage assumptions, and contract changes. A missing or unreliable input must fail closed into No-Trade rather than silently invent a value.
If multiple exit conditions occur within the same observation interval, their precedence and the applicable fill assumptions must be explicitly defined and versioned before backtesting. The backtest must not resolve simultaneous or unobservable exits using an unstated favorable ordering.

## Signal Event-Time / Anti-Look-Ahead Contract

Every strategy decision has a signal event timestamp. All strategy inputs used at that event, including underlying OHLCV, option OHLCV or quotes, opening-range values, ATR, VWAP, volume, open interest, Greeks, trend, key levels, contract metadata, expiry status, market calendar state, and risk state, must be derived only from information available at or before that timestamp. A value from a candle is unavailable until the applicable candle has closed under the selected bar convention.

Historical data selection must also be causal: only contracts, prices, quotes, volume, open interest, metadata, and market or expiry-calendar facts that were historically available and applicable at the signal event may be used. No future high, low, close, volume, open interest, quote, contract status, expiry outcome, or later corrected record may influence a historical signal unless the correction's availability time is explicitly modeled. Missing, invalid, or ambiguous evidence must fail the relevant gate or result in `REVIEW`/`NO-TRADE`; it must never be replaced with a favorable default.

The event timestamp, data cut-off, bar-close convention, feature versions, and candidate-selection inputs must be recorded so that every signal can be replayed deterministically.

## Data and Processing Boundaries

The intended research flow is:

`data -> data_qc -> strategy -> backtest`

The intended live-capable flow is separate:

`strategy -> risk -> execution -> broker`

The backtest layer must not access live broker order functions. Shared signal and risk logic must have explicit interfaces and deterministic inputs so that reuse does not create an execution shortcut.

## Validation Requirements

Historical data must pass Data QC before optimization. Backtesting must model realistic fills, spreads, slippage, brokerage and other applicable charges, contract and lot-size changes, and trading-session changes.

Validation must address look-ahead bias, survivorship bias, and data snooping. Use development/in-sample, out-of-sample, and walk-forward validation. Stress tests must cover slippage, entry delay, exit delay, spread widening, and degradation in win/loss characteristics. Prefer parameter plateaus over isolated optimization peaks. Use Monte Carlo or trade-order randomization where appropriate.

## Safety and Change Control

Any change to market logic, score components, risk controls, data assumptions, or execution semantics must identify the affected specification and include tests or research evidence appropriate to the change. Agents and developers must report assumptions before architectural changes and must not silently change trading rules.

## Deferred Work

Broker integration, live deployment, monitoring automation, and production parameter selection remain deferred until the applicable development phases in `DEVELOPMENT_PLAN.md` have been completed with documented evidence. Paper-trading infrastructure exists for controlled replay/stream testing, but paper trading does not constitute validation or authorization for live orders.

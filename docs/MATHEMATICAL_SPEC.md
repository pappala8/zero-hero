# Zero Hero Mathematical Specification

## Purpose

This document defines the mathematical vocabulary and causal calculation rules for Zero Hero research. It specifies formulas and invariants, not final optimized parameter values. Every calculation must use data available at or before the calculation timestamp.

## Time and Indexing

Let bars be indexed by event time `t`, in chronological order, with timestamps normalized to IST for session rules. A bar's event timestamp is the time when its completed observations become available for decision-making, not the time when the interval starts. A feature used to make a decision at `t` may depend on observations at times `<= t`, subject to the bar-close or tick-availability convention selected by the backtest.

A signal generated from a completed bar must not use that bar's future high, low, close, volume, or any later record. If an opening range is defined by 09:15-09:30, the breakout decision must occur only after the range is complete and the range values are known.

## Opening Range

For the opening-range set `R_d` on trading day `d`:

$$
OR_H(d) = \max_{t \in R_d} High_t
$$

$$
OR_L(d) = \min_{t \in R_d} Low_t
$$

$$
OR_W(d) = OR_H(d) - OR_L(d)
$$

The initial opening range is 09:15-09:30 IST. For the initial 5-minute timeframe, it consists exactly of the three completed intervals 09:15-09:20, 09:20-09:25, and 09:25-09:30 IST. The event timestamps for those completed bars are 09:20, 09:25, and 09:30 IST respectively. Breakout evaluation must not use the final 09:25-09:30 candle before that candle has closed; the opening-range values become available only at or after 09:30 IST under this convention.

## Volatility

For bar `t`, true range is:

$$
TR_t = \max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)
$$

ATR is an explicitly versioned aggregation of true range over a configurable lookback. The ATR method and lookback are research parameters. During the warm-up period, when fewer than the required observations are available, ATR is insufficient and must be marked unavailable. The implementation must not shorten the lookback, backfill future observations, or substitute a favorable value unless that behavior is an explicitly versioned research decision. The initial normalized opening-range measure is:

$$
OR\_ATR(d) = \frac{OR_W(d)}{ATR(d)}
$$

A zero or unavailable ATR is invalid for ratio-based logic and must result in a rejected feature or No-Trade outcome, not a fabricated ratio.

## VWAP

For a session prefix ending at `t`, using price `P_i` selected by configuration and traded volume `V_i`:

$$
VWAP_t = \frac{\sum_{i \le t} P_i V_i}{\sum_{i \le t} V_i}
$$

The price convention, session reset, treatment of zero volume, VWAP source, and dependency on the available volume source are research and data decisions. In particular, whether VWAP uses underlying or option data must be selected, versioned, and recorded before comparison of results; it is not a fixed assumption of this specification.

## Structural Breakout Hypotheses

An upside breakout candidate crosses a defined upper structural level, initially related to `OR_H`; a downside candidate crosses a defined lower structural level, initially related to `OR_L`. Confirmation may combine distance, VWAP, volume, trend, key levels, and option quality.

The following are hypotheses, not final rules:

- CALL direction is supported when price is structurally above the upper level and VWAP alignment is positive.
- PUT direction is supported when price is structurally below the lower level and VWAP alignment is negative.
- A failed breakout is a candidate that loses the structural condition within a configured confirmation or invalidation window.

Breakout distance, confirmation duration, failed-breakout definition, trend estimator, and key-level definitions require empirical calibration.

## Score

For components `c_i` with conceptual weights `w_i`, each component is a normalized value:

$$
0 \le c_{i,t} \le 1
$$

The conceptual weights are non-negative and sum to 100. The score is:

$$
ZHScore_t = \sum_i w_i c_{i,t}
$$

The initial weights are Compression 20, Breakout 20, VWAP 15, Volume 15, Trend 10, Key level 10, and Option quality 10. Component normalization, missing-input handling, and score threshold are configurable research decisions. Missing or invalid evidence must not receive a favorable or default component value; it must fail the relevant gate or result in `REVIEW`/`NO-TRADE`. No threshold is designated as final.

## Option Candidate Representation

A contract identity is the tuple:

$$
Contract = (Underlying, Expiry, Strike, Right)
$$

where `Right` is exactly `CE` or `PE`. A candidate must be tradable at its selection timestamp, have a historically valid expiry and strike, and satisfy the configured liquidity and data-quality gates.

Initial moneyness candidates are ATM, 1 OTM, and 2 OTM relative to the selected underlying reference. The mapping from underlying price to strike increment, rounding convention, and expiry selection must be deterministic, explicit, and versioned. The same versioned mapping rule must produce the same candidate strikes for the same timestamp, underlying reference, contract specification, and expiry calendar.

## Return and P&L Accounting

For a long option position with causal fill prices `P_e^{fill}`, `P_x^{fill}`, quantity `Q`, and total transaction costs `C`:

$$
GrossPnL = (P_x^{fill} - P_e^{fill}) Q
$$

$$
NetPnL = GrossPnL - C
$$

Fill-price modelling and transaction-cost accounting are separate. Entry and exit fill prices must be generated by a causal fill model that explicitly models applicable market price, bid-ask spread, slippage, delay, liquidity, and gap behavior. Transaction costs `C` may include brokerage, taxes, exchange charges, and other applicable charges, but must not include slippage or spread components already applied to fill prices. The backtest must not double count either fill effects or transaction costs.

## Risk Sizing

Fixed fractional risk is represented conceptually as:

$$
RiskBudget_d = Equity_d \times RiskFraction
$$

Define `StopRiskPerUnit` as the loss per option unit between the causal entry price and the selected stop outcome, including any explicitly included per-unit risk adjustment. Define `LotSize` as the number of option units in one tradable lot. Define `Lots` as a non-negative integer number of lots. Define `Quantity` as the number of option units:

$$
Quantity_{risk} = \frac{RiskBudget_d}{StopRiskPerUnit}
$$

$$
Lots_{risk} = \left\lfloor \frac{Quantity_{risk}}{LotSize} \right\rfloor
$$

$$
Quantity_{rounded} = Lots_{risk} \times LotSize
$$

The capital constraint is applied after lot-size rounding:

$$
Lots_{capital} = \left\lfloor \frac{CapitalAvailable}{P_e^{fill} \times LotSize} \right\rfloor
$$

$$
Lots = \min(Lots_{risk}, Lots_{capital}), \qquad Quantity = Lots \times LotSize
$$

The exact capital model may include explicitly documented margin, charges, and reserve requirements. `RiskFraction`, stop definition, lot-size rules, and capital constraints are research parameters until validated. Zero or invalid stop risk, unavailable lot size, or insufficient evidence must result in no position rather than a favorable quantity.

## Exit Semantics

Exit reasons must be mutually traceable to observable events, including structural stop, premium-loss protection, time exit, end-of-day exit, risk halt, and data or execution failure. Exit timestamps and prices must obey the same availability and fill conventions as entries.

If a bar or observation interval moves through a stop level, including a gap-through-stop, the backtest must use an explicitly documented gap-through-stop rule and fill assumption. It must define whether the stop fills at the stop price, the first available tradable price, a modeled quote, or another conservative rule. The rule must be selected before backtesting and must not depend on the eventual favorable outcome.

## Invariants and Causal Tests

Implementations must test at least the following invariants:

- timestamps are strictly ordered within a contract/session after duplicate handling
- `High >= max(Open, Close, Low)` and `Low <= min(Open, Close, High)`
- no signal references a record after its decision timestamp
- no position remains open beyond the configured forced end-of-day boundary
- contract identity is stable across a trade
- P&L reconciles to quantity, prices, and costs
- a missing required feature cannot silently become a favorable value

These equations do not authorize a trading rule by themselves; rule acceptance requires the validation process defined in the system, QC, and development-plan documents.

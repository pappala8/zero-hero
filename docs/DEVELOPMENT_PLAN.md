# Zero Hero Development Plan

## Guiding Constraints

## Current Project Status

The repository has implemented research-engineering scaffolding across the strategy, paper-trading, normalization, QC, release, and sample-adapter layers. The current gate is not complete: the real sample does not yet have a QC-approved normalized release, and documentation/release evidence must remain aligned with the implementation. Strategy Engine V0.1 and paper-trading components are implementation scaffolding, not calibrated or validated trading rules. No backtest, optimization, broker integration, or live deployment is authorized by the current state.

**Current next work:** complete P0 QC/release/documentation reconciliation, then address the remaining causal strategy/risk semantics and tests before historical backtesting.


This plan sequences research before automation. No phase authorizes live orders by itself. The project must preserve causal signals, reproducible backtests, explicit assumptions, and separation between research/backtest and live execution.

Numerical thresholds are research parameters until empirical validation. No historical data will be downloaded or purchased as part of this documentation task.

- P1-D paper risk/exit semantics implemented: structural stop is derived from the entry OR boundary and stored with the position; stop exits use observed underlying bars; gap-through-stop exits use the observed option price rather than a synthetic stop price; option exit prices must be causal and contract-matched; exit precedence remains explicit.

## Phase 0: Repository and Environment

Confirm the existing architecture, runtime policy, dependency policy, test strategy, configuration approach, secret handling, and reproducible environment. This phase is complete for the current repository baseline; later implementation is governed by the subsequent phases and gates.

**Exit evidence:** documented environment and repository checks; no secrets committed.

## Phase 1: Specifications

Complete and review the system, mathematical, data, QC, and development specifications. Resolve terminology, causal timing, fixed requirements, hypotheses, configurable parameters, and deferred features. Reconcile documentation with the implementation status and preserve explicit phase gates.

**Exit evidence:** approved documentation with no unresolved architectural contradiction.

## Phase 2: Historical Data Provider Evaluation

Evaluate candidate providers for NIFTY spot and option 1-minute OHLCV, expired contracts, relevant strikes, OI, licensing, provenance, API/file access, and historical completeness. Also evaluate NIFTY futures as a preferred research reference for VWAP reference, volume, market regime, and price discovery. NIFTY futures are not mandatory for the minimum feasibility dataset. Do not select based only on current-contract availability.

**Exit evidence:** provider comparison, licensing decision, sample schema, and known limitations.

## Phase 3: Sample Data Acquisition

Acquire only a licensed, appropriately scoped sample after Phase 2 approval. Validate ingestion and provenance; do not claim strategy validity from the sample.

**Exit evidence:** reproducible sample manifest and raw-data handling record.

## Phase 4: Data QC

Implement the QC gate for schema, timestamps, sessions, missingness, duplicates, OHLC, prices, volume/OI, contract identity, expiry, strike, right, gaps, staleness, continuity, and reference comparisons where available.

**Exit evidence:** machine-readable result and human-readable report with PASS/FAIL/REVIEW status.

## Phase 5: Normalized Data Layer

Define a versioned, traceable normalized representation for underlying bars, option bars, contracts, calendars, costs, lot sizes, and provenance. Preserve historical identity and effective-date changes.

**Exit evidence:** deterministic normalization replay and QC-approved normalized release.

## Phase 6: Backtest Engine

Build a research-only engine with causal event ordering, realistic fill assumptions, spread/slippage, charges, lot-size and session changes, forced end-of-day exits, and audit trails. Backtest must not import or call live broker order functions.

**Exit evidence:** unit and scenario tests, reproducible results, and bias-focused validation cases.

## Phase 7: Zero Hero Strategy Implementation

Implement the documented 5-minute research hypothesis using the opening range, VWAP, structural breakout confirmation, failed-breakout filtering, ZH score, option candidates, and No-Trade outcome. Keep Daily ZH and Expiry ZH separately configurable and observable.

**Exit evidence:** tests for causal features, signals, option selection, rejection reasons, and risk handoff.

## Phase 8: Calibration and Validation

Calibrate only through explicit research tasks. Use development/in-sample, out-of-sample, and walk-forward validation, with stress tests for slippage, delays, spread widening, and degraded win/loss characteristics. Prefer plateaus and use Monte Carlo/trade-order randomization where appropriate.

**Exit evidence:** frozen experiment definitions, validation reports, robustness analysis, and documented investigation of isolated optimization peaks.

## Phase 9: Paper Trading

Replay or stream data into a non-ordering paper environment. Compare expected signals, simulated fills, data latency, and operational behavior without broker order placement.

**Exit evidence:** paper-trading report, reconciliation, and incident procedures.

## Phase 10: Broker Integration

Only after prior gates, design the separate execution and broker adapter boundary. Add credential handling outside source control, order-state reconciliation, rate-limit handling, and explicit kill switches. Research/backtest paths remain unable to place broker orders.

**Exit evidence:** integration tests using mocks or sandbox facilities, security review, and operational runbook.

## Phase 11: Controlled Small-Capital Live Deployment

Deploy with approved, limited capital, explicit risk caps, monitoring, manual intervention, and rollback procedures. Treat live behavior as a new validation environment; do not silently alter strategy rules.

**Exit evidence:** staged deployment review and incident-free acceptance period.

## Phase 12: Monitoring and Scaling

Monitor data health, signal rates, execution quality, slippage, P&L, risk limits, drift, and system incidents. Scale only after evidence supports it, with periodic revalidation and change control.

**Exit evidence:** monitoring dashboards/reports, alert policies, and a documented scaling decision.

## Change Control

Every phase must record assumptions, inputs, code/configuration versions, results, unresolved risks, and exit decision. A failed gate returns work to the responsible phase; it does not justify bypassing QC or validation.

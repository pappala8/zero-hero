# Zero Hero Data Requirements

## Purpose and Status

This document defines the data needed for research, realistic backtesting, and later controlled deployment. It does not authorize downloading, purchasing, or connecting to any provider. Provider and licensing decisions belong to the development plan.

Mandatory fields are required for a dataset to support the corresponding research stage. Preferred fields improve realism but are not assumed to exist.

## Mandatory Research Data

### NIFTY 50 Spot

- 1-minute OHLC, with timestamp, explicit timezone, and session metadata
- instrument identity and source metadata
- trading-session and holiday context
- sufficient history for development, out-of-sample, and walk-forward periods

Conventional traded volume must not be assumed to exist for the NIFTY 50 index itself. If a provider supplies a NIFTY spot volume field, its meaning, source, construction, and limitations must be explicitly documented and validated. The system must never fabricate NIFTY spot volume from another instrument. Volume-dependent features must use a separately specified tradable proxy when valid NIFTY spot volume is unavailable.

### NIFTY Options

- 1-minute OHLCV for all relevant strikes and expiries that were actually tradable
- timestamp and explicit timezone
- underlying identifier
- expiry
- strike
- right: exactly CE or PE
- volume
- open interest
- historical expired contracts, not only currently listed contracts
- source, retrieval time, adjustment policy, and licensing metadata

Contract identity must include `(underlying, expiry, strike, CE/PE)`. A display symbol alone is not sufficient as a durable historical identity.

### NIFTY Futures - Preferred Research Reference

NIFTY futures are preferred research data that may be evaluated as a market-reference source for VWAP, volume, regime, and price-discovery analysis. They are not mandatory for the minimum feasibility dataset.

Preferred fields:

- 1-minute OHLCV
- timestamp and timezone
- expiry
- volume
- OI where available
- historical expired contracts
- contract identity
- lot size
- contract specification metadata
- continuous-contract construction rules, if a continuous series is supplied

## Preferred Data

Where reliably available, collect:

- implied volatility
- delta, gamma, theta, and vega
- trade or quote quality indicators
- bid and ask or quote snapshots for realistic execution research
- provider API access
- CSV and/or Parquet export
- clear licensing that permits algorithmic research and required retention

The system must distinguish observed values from theoretical or reconstructed values. Historical traded option prices are preferred to theoretical repricing for execution simulation. If only OHLC data is available, the backtest must use a documented conservative execution and fill model and clearly identify the limitation. OHLC-only data must not be represented as providing exchange-realistic fills.

## Temporal Coverage

Data coverage must document:

- first and last timestamp
- trading days included and excluded
- expiry calendar coverage
- contract and lot-size changes
- market-session changes and holidays
- interruptions, outages, and provider restatements
- whether all historically tradable strikes are represented

For options, "all relevant strikes" means all strikes required to reconstruct the strategy's candidate universe at each historical signal timestamp, plus sufficient surrounding strikes to validate ATM/OTM mapping and liquidity selection. The dataset must not be prematurely restricted to only current ATM +/- 2 strikes when broader chain data is available.

A dataset with only surviving current contracts is insufficient for unbiased historical option research. Preferred historical coverage is at least 3 years of complete, usable history, with longer coverage beneficial when data quality is maintained. Final development, out-of-sample, and walk-forward date partitions are deliberately not defined here; they will be selected after Data QC and before parameter optimization.

## Timestamp Requirements

Every record must have a parseable timestamp and declared timezone or a documented normalization rule. Session logic uses IST. The provider's timestamp semantics, bar closing convention, and daylight-saving behavior, if any, must be recorded.

The dataset must make it possible to determine whether information was available before a signal. Ambiguous timestamps are a QC failure or review item, not a value to be guessed.

## Market and Contract Semantics

The normalized data layer must preserve, or be able to reconstruct:

- OHLC relationships
- traded volume and OI semantics
- expiry date and expiry type where relevant
- strike precision and increments
- CE/PE right
- instrument status and tradability
- corporate or index methodology events if relevant
- lot size and contract specifications by effective date

Historical NIFTY option prices must not be silently back-adjusted. Index methodology changes and contract-specification changes must be preserved as historical metadata where relevant. Equity-style corporate-action adjustments must not be applied to NIFTY option prices unless explicitly justified and documented. Any permitted adjustment must be documented and represented in metadata without obscuring the raw source.

## Research Dataset Tiers

### Minimum Feasibility Sample

A small, licensed sample used to validate schema, ingestion, QC, normalization, and basic backtest mechanics. It is not sufficient to claim strategy validity.

### Research Dataset

Coverage adequate for development and out-of-sample periods, with expired contracts, relevant strikes, and required metadata.

### Validation Dataset

A time-separated holdout and/or walk-forward segment protected from parameter selection until the relevant experiment is frozen.

## Provenance and Storage

The research data pipeline is:

`RAW SOURCE -> DATA QC -> NORMALIZED DATA -> FEATURES -> BACKTEST`

Raw vendor data must be treated as immutable source data and must never be silently overwritten. Corrections, normalization, and transformations must occur downstream and remain traceable to the raw source.

Each dataset release must record provider, license, retrieval date, source query or files, timezone, schema version, checksum where practical, and transformations. Raw proprietary data must not be committed to the repository. Access paths and credentials must be provided through local configuration or environment variables, never source control.

Normalized derived data must remain traceable to its raw source without embedding restricted raw data in generated artifacts.

## Acceptance Criteria

Data is ready for downstream research only when:

1. the required schema is present and versioned;
2. timestamps and session boundaries are unambiguous;
3. historical contracts and identities are represented correctly;
4. Data QC produces an acceptable machine-readable result and report;
5. licensing and retention constraints are documented; and
6. the dataset supports a reproducible, causal replay; and
7. vendor samples have been tested for historical option-chain completeness, including ATM, ITM, OTM, subsequently expired contracts, historical OI, historical volume, expiry, strike, and contracts actually tradable at the relevant timestamp.

A failed or unresolved QC result blocks optimization and strategy conclusions.

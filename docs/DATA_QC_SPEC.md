# Zero Hero Data QC Specification

## Purpose

Data QC is a release gate between ingestion and research. A dataset that has not passed QC must not be used for strategy optimization or performance claims.

QC must produce both machine-readable results and a human-readable report. Every check has a status: `PASS`, `FAIL`, or `REVIEW`.

## Check Categories

### Schema

Verify required columns, data types, nullability, units, schema version, and file or partition readability. Unknown columns may be preserved but must not silently replace required fields.

### Timestamps and Timezone

Check parsing, timezone declaration, monotonic ordering within contract/session, valid market dates, bar interval semantics, and conversion to IST. Ambiguous or mixed timezone data is `FAIL` until resolved.

### Trading Sessions

Check session start and end boundaries, expected bars by session and interval, holidays, special sessions, and expiry-session differences. Unexpected bars outside the applicable session are `REVIEW` or `FAIL` depending on impact.

### Missing Candles and Continuity

Identify missing bars, unexplained gaps, partial sessions, provider outages, and discontinuities. Classify expected market closures separately from missing observations. Required gaps in a signal or fill window block affected research.

### Duplicate Rows

Detect exact and key-based duplicates. Key uniqueness must include the timestamp and contract identity where applicable. Conflicting duplicates are `FAIL`; identical duplicates must be removed or explicitly handled and counted.

### OHLC and Price Validity

Check:

- `High >= max(Open, Close, Low)`
- `Low <= min(Open, Close, High)`
- non-negative or otherwise instrument-valid prices
- finite numeric values
- zero or missing prices according to an explicit instrument policy
- no impossible option premium records

### Volume and Open Interest

Check non-negative values, type and unit consistency, unexpected resets, extreme spikes, stale values, and provider-specific reporting conventions. An anomaly may be `REVIEW` when it is plausible but requires source confirmation.

### Contract Identity

Check underlying, expiry, strike, and right. CE/PE must be canonical. A record without a stable identity cannot be joined safely to a contract history.

### Expiry and Strike Correctness

Check expiry against the applicable calendar, strike precision and increment, expiry ordering, and whether the contract was actually tradable at the record timestamp. Expired contracts must remain historically identifiable without being treated as live contracts.

### Suspicious Price Gaps

Detect gaps that exceed configurable research screening ranges, abrupt price changes inconsistent with neighboring records, and gaps associated with missing data or contract changes. This check flags candidates for review; it does not erase observations automatically.

### Stale and Missing Records

Detect repeated unchanged prices or quotes, stale OI, missing option records during expected activity, and suspiciously sparse instruments. Staleness rules are configurable and must be reported with affected contracts and periods.

### Option-Chain Completeness

Where applicable, compare the expected option chain with observed data for:

- expected strikes versus observed strikes
- CE/PE presence
- all relevant expiries
- historical expired contracts
- ATM, ITM, and OTM availability
- gaps in contract history
- contracts disappearing or reappearing unexpectedly
- historical tradability at the relevant timestamp

The check must report affected dates, expiries, strikes, and contracts. Missing chain coverage that can bias candidate selection, liquidity evaluation, or historical replay must be identified as blocking or non-blocking according to its documented research impact.

### Cross-Instrument Consistency

Diagnose suspicious relationships between underlying and option observations, including:

- underlying movement with suspiciously unchanged option prices
- option price movement without corresponding market context
- suspicious option-price relationships
- expiry/strike contracts behaving inconsistently

These checks are diagnostic. They should normally produce `REVIEW` rather than automatically declaring valid market behavior invalid. Any escalation to `FAIL` must be based on documented evidence that the issue can bias the intended research.

### Trading-Day Completeness

Compare expected trading days from the applicable historical market calendar against observed data. Distinguish among expected market closure, scheduled holiday, special session, provider outage, and unexplained missing trading day. An unexplained missing trading session is a blocking issue when it affects the intended research period.

### Raw-Data Integrity

Where practical, check and record raw-source integrity using:

- file checksum
- file size
- row counts
- partition identifiers
- source metadata
- ingestion or retrieval timestamp
- schema version

The QC result must identify exactly which raw release was evaluated.

### Reference Comparison

Where authoritative reference data is available, compare timestamps, OHLC, volume, OI, expiry, strike, and contract counts. Record source, comparison window, tolerance, and discrepancies. Reference comparison cannot be performed is a `REVIEW`, not an unreported assumption.

## No Silent Data Repair

QC must not silently modify, interpolate, forward-fill, backfill, delete, or otherwise repair raw source data. QC may identify, quarantine, or report invalid records. Any transformation required for normalized research data must occur in a separately versioned downstream transformation step and remain traceable to the raw source.

## Status Policy

- `PASS`: the check was completed and no blocking issue was found.
- `FAIL`: a required check failed or an issue can bias signals, fills, contract selection, or P&L. Downstream optimization is blocked.
- `REVIEW`: evidence is incomplete or an anomaly needs a documented decision. Research use requires explicit approval and scope.

The final release decision is deterministic and versioned:

- `PASS`: all mandatory checks are `PASS` and no blocking `REVIEW` remains.
- `FAIL`: any mandatory check is `FAIL`, or any blocking unresolved `REVIEW` exists.
- `REVIEW`: no mandatory check is `FAIL`, but one or more non-blocking `REVIEW` items remain.

Thresholds for anomaly detection must be configuration and must not be changed to make a dataset pass without recording the change. The release result must record the decision logic or policy version used.

## Output Contract

### Machine-Readable Result

The result must include at least:

- dataset identifier and version
- source and provenance reference
- schema version
- QC execution timestamp
- timezone and session calendar
- check name and category
- status
- counts and affected partitions/contracts
- thresholds and configuration version
- error or anomaly details
- tool/version metadata

JSON is the initial recommended format; a tabular form may accompany it.

### Human-Readable Report

The report must summarize scope, coverage, checks, status counts, blocking failures, review items, missingness, duplicate counts, anomalies, reference comparisons, and the release decision. It must identify exactly which data release was evaluated.

## Reproducibility and Auditability

QC must be deterministic for the same input, configuration, calendar, and code version. Raw inputs must remain traceable by path or checksum without committing proprietary data. Any manual disposition of a `REVIEW` item must record the reason, owner, date, and affected scope.

## Gate

No strategy optimization, final parameter selection, or performance claim may use a dataset whose QC release decision is `FAIL` or unresolved `REVIEW`.

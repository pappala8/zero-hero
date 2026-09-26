# Zero Hero

Zero Hero is a research-first system for automated intraday NIFTY 50 option buying. Its initial research timeframe is 5-minute, with 1-minute underlying and option data preferred for realistic execution simulation.

## Current Stage

The repository is in the research-engineering stage. The system specifications, mathematical definitions, data requirements, QC contract, strategy/paper-trading scaffolding, normalized-data layer, and release infrastructure are implemented and under test.

The current implementation includes a research-only Strategy Engine V0.1, paper-trading/ledger infrastructure, provider-neutral normalization/QC, and a sample-data adapter. There is no broker connection, live order placement, or production deployment. Numerical thresholds remain research parameters until validated.

## Architecture

Research flow:

`data -> data_qc -> strategy -> backtest`

Separate execution flow:

`strategy -> risk -> execution -> broker`

Backtest and research code must never directly access live broker order functions. The system supports Daily ZH, Expiry ZH, and No-Trade / Capital Protection modes. No-trade is a valid result; the system must not force a trade.

## Safety Boundary

Signals and features must use only information available at the signal timestamp; no look-ahead is permitted. Historical data must pass Data QC before optimization or performance claims. Historical option research must preserve contract identity and correctly represent expired contracts. Backtests must model realistic fills, spreads, slippage, charges, contract changes, and session changes, and must use development, out-of-sample, and walk-forward validation.

Secrets, credentials, access tokens, and proprietary raw datasets must stay outside source control. Broker integration is deferred until the documented research, QC, validation, and paper-trading gates are complete.

## Documentation

- [System specification](docs/ZERO_HERO_SPEC.md)
- [Mathematical specification](docs/MATHEMATICAL_SPEC.md)
- [Data requirements](docs/DATA_REQUIREMENTS.md)
- [Data QC specification](docs/DATA_QC_SPEC.md)
- [Development plan](docs/DEVELOPMENT_PLAN.md)
- [Copilot instructions](.github/copilot-instructions.md)


## Current paper-risk contract

Position sizing derives stop risk from the selected option's causal delta/gamma and the structural opening-range stop. Caller-supplied `stop_risk_per_unit` is deprecated and ignored. The approximation is explicitly versioned as `DELTA_GAMMA_STRUCTURAL_STOP_V1`; missing delta fails closed. Structural-stop exit decisions are derived from causal underlying bars, not caller boolean flags.

The paper journal uses an append-only hash chain and the stored-session wrapper rolls back in-memory state if durable journal append fails.

### P1-G final remediation

The current snapshot includes transactional journal rollback, bounded streaming dataset release, and a derived stop-risk API. See `docs/P1G_FINAL_REMEDIATION.md`.

# Zero Hero

Zero Hero is a research-first system for automated intraday NIFTY 50 option buying. Its initial research timeframe is 5-minute, with 1-minute underlying and option data preferred for realistic execution simulation.

## Current Stage

The repository is currently in the documentation and research-foundation stage. The system specifications, mathematical definitions, data requirements, QC contract, development sequence, and agent guardrails are being established before Python implementation.

No strategy implementation, broker connection, order placement, historical-data purchase, or live deployment is included at this stage. Numerical thresholds remain research parameters until validated.

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

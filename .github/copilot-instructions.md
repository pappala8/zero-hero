# Zero Hero Copilot Instructions

Before coding, read the relevant documents in `docs/`, especially `ZERO_HERO_SPEC.md`, `MATHEMATICAL_SPEC.md`, `DATA_REQUIREMENTS.md`, `DATA_QC_SPEC.md`, and `DEVELOPMENT_PLAN.md`.

- Obey the documented architecture: `data -> data_qc -> strategy -> backtest` and the separate `strategy -> risk -> execution -> broker` path.
- Make the smallest necessary change and preserve the existing directory architecture.
- Never silently change trading rules, risk controls, data assumptions, or execution semantics.
- Treat numerical thresholds as research ranges unless a document explicitly marks them fixed.
- Never optimize strategy parameters without an explicit research task and validation plan.
- Follow the phase sequence defined in `docs/DEVELOPMENT_PLAN.md`. The repository already contains research-engineering scaffolding implemented across the strategy, paper-trading, normalization, QC, and release layers; do not interpret that implementation as phase-gate approval. Do not add backtesting, broker integration, or live execution until the documented prerequisite gates are satisfied.
- Use only information available at the signal timestamp; do not introduce look-ahead or survivorship bias.
- Keep backtest and research isolated from live execution. Never connect research code to live order execution or place broker orders from a backtest.
- Write tests for non-trivial logic, especially time alignment, contract identity, QC, fills, risk, and causal signal behavior.
- Preserve deterministic and reproducible backtests by recording inputs, configuration, versions, and random seeds where applicable.
- Do not describe a strategy, parameter set, backtest, or research result as profitable, validated, robust, production-ready, or effective based only on in-sample results, a limited sample, or incomplete validation. Clearly distinguish observations, hypotheses, backtest results, and validated conclusions.
- Never expose API keys, access tokens, passwords, credentials, proprietary data, or other secrets in source, tests, logs, or documentation.
- Before making a change that could affect trading behavior, identify which specification document governs the change and report the affected requirement.
- Report assumptions before making architectural changes.
- If requirements are ambiguous, record the ambiguity and choose a conservative, explicit behavior rather than inventing a final trading rule.

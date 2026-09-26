# Zero Hero P1-F Audit Remediation

## Implemented

- Position sizing no longer accepts caller-controlled structural-stop risk. Stop risk is derived from the selected option's causal delta/gamma and the structural opening-range stop under `DELTA_GAMMA_STRUCTURAL_STOP_V1`.
- Missing option delta fails closed before entry sizing.
- Caller-supplied `structural_stop_triggered` is deprecated and ignored for exit decisions.
- Structural stop is derived only from the stored OR stop and causal underlying observations.
- Future option snapshots are rejected before exit-condition evaluation.
- Paper fill behavior is explicitly versioned as `OBSERVED_PRICE_PLUS_EXPLICIT_SLIPPAGE_V1`; it is a paper abstraction, not a bid/ask reconstruction.
- Paper journal entries now use a SHA-256 predecessor hash chain.
- Stored paper-session mutations roll back in-memory state if durable journal append fails.
- QC continuity is strict for underlying/futures and non-blocking review for options unless an explicit expected-contract universe is supplied.
- QC reports are persisted as `qc_report.json` beside the normalized release manifest and validated byte-for-byte against the supplied QC report.
- Release validation checks manifest, QC artifact, partition existence, partition hashes, row counts, and aggregate content hash.
- Pytest collection now has a project-level `tests/conftest.py`; the PyArrow-dependent sample adapter test is skipped cleanly when PyArrow is unavailable.

## Verification

From the project root:

```text
PYTHONPATH=. pytest -q
```

Current environment result: **94 passed, 1 skipped**. The skipped test group requires PyArrow; `requirements.txt` declares `pyarrow>=15`.

The skip is environmental only. On a development machine with `pip install -r requirements.txt`, the sample adapter tests should execute rather than skip.

## Not claimed

This remediation does not establish live-trading readiness, profitability, or historical strategy validity. Bid/ask execution quality, option-chain universe derivation, and historical calibration remain research/data-validation work.

# Zero Hero P1-G Final Remediation

This release closes the P1-F verification findings.

## Transactional paper state

`JournaledPaperSession.ingest()` and `close_session()` now snapshot the ledger before mutation and restore it if journal append fails. The journal remains append-only and hash chained. This prevents an in-memory ledger transition from surviving when its corresponding journal entry is not durable in the session boundary.

`StoredPaperSession` continues to protect the higher storage boundary by rolling the full session back if the filesystem append fails.

## Streaming release

`NormalizedReleaseStore.publish()` no longer materializes the complete input iterable. It consumes bounded batches, groups each batch by session/instrument, stages deterministic chunks, and delegates final assembly to the external-merge `publish_staged()` path.

The remaining `tuple(records)` usage is confined to `stage_partition()`, where it is a bounded caller-supplied chunk rather than the complete release.

## Risk API hardening

The public risk request now accepts `DerivedStopRisk` rather than an arbitrary `stop_risk_per_unit` value. The paper engine constructs this object from the causal structural stop, selected option delta/gamma and the configured risk-model version. A model-version mismatch fails closed.

The legacy event field `stop_risk_per_unit` remains only as a compatibility field in serialized paper events and is ignored for risk sizing.

## Verification

Project-root test command:

```text
pytest -q
```

Result in the audit environment:

- 96 passed
- 1 skipped

The skipped test requires the optional PyArrow dependency; `requirements.txt` declares it. The codebase also passes Python bytecode compilation with `python -m compileall -q .`.

This release is the final remediation snapshot for the P1-F audit findings. It is not a claim of live-trading readiness; real-time data adapter, paper-market observation, historical validation and broker integration remain separate phases.

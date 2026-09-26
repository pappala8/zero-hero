# P2-A — Real-Time Market Data Contract

P2-A defines the provider-neutral boundary for real-time Zero Hero market data. It does not connect to Zerodha and contains no order-placement capability.

## Causality

Every event carries `event_timestamp` and `available_at`. Both are timezone-aware, and `available_at` cannot precede the event timestamp.

## Identity

The initial scope is NIFTY. Options require `(underlying, expiry, strike, right)` and CE/PE plus expiry remain distinct. Provider instrument tokens are external identifiers and are not the strategy's contract identity.

## Validation

The layer fails closed for missing provider/source IDs, invalid tokens, malformed contract identity, invalid OHLC relationships, negative/non-finite prices, volume or open interest, and invalid source sequence values.

## Provider boundary

`MarketDataProvider` exposes connection, subscription and event-stream operations only. It deliberately has no order-placement methods. Broker-specific adapters belong in a later P2-B phase.

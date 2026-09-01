# Architecture

## Boundary

```text
candidate producer
      |
      v
julesctl CLI
      |
      v
application services ---- SQLite state store
      |
      v
Jules v1alpha REST adapter
      |
      v
Jules session ----> GitHub PR ----> independent review/CI/merge
```

The CLI, future MCP adapter, and any service wrapper must call the same application services. There is one implementation of pagination, dispatch reconciliation, activity receipt handling, and deletion policy.

## Contracts

The Google wire contract and the `julesctl` machine contract are deliberately separate. Request objects are strict. Response objects allow additive fields and preserve raw state strings. A future Google state must make fleet inspection more informative, not crash it.

## Dispatch transaction

`dispatch_key` identifies the desired work object. The fingerprint identifies the exact resolved request. `attempt_id` identifies one possible remote mutation. `session_id` identifies Jules's remote session.

A dispatch attempt is persisted before `POST /sessions`. The POST is sent once. If the result is uncertain, `julesctl` scans remote sessions and adopts only a unique match; zero or multiple matches remain indeterminate and keep their local reservation.

## Single-host authority

The first deployment uses one SQLite database on one trusted controller host. This gives process-safe local ownership, not distributed exactly-once semantics. Multiple independent controller hosts require a later shared service, not copied SQLite databases.

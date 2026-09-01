# ADR 0001: direct Jules REST adapter

Status: accepted

Use a narrow Python `httpx` adapter over the Jules v1alpha REST API. Treat `cjules`, Google Labs SDK/MCP, and other clients as comparison implementations rather than runtime dependencies.

The deciding requirement is control of non-idempotent write semantics and forward-compatible wire preservation. A wrapper that must replace transport retries, mapping, persistence, and reconciliation is not materially thinner than the direct adapter.

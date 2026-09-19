# Changelog

All notable changes to `julesctl` are documented here.

The project follows semantic versioning after `0.1.0`; pre-release contracts may change with an explicit migration note.

## Unreleased

### Added

- Direct Python Jules `v1alpha` REST adapter.
- Exact source resolution and guarded pagination.
- SQLite-backed work identity, dispatch attempts, session ownership, activity receipts, and deletion plans.
- One-attempt session creation with uncertain-outcome reconciliation.
- Keyless local fleet freeze and credential-isolated candidate submission.
- Stable JSON/JSONL operation contracts and cross-platform CI.

### Security

- API keys are environment-only.
- HTTP proxy inheritance and redirects are disabled for Jules requests.
- Destructive fleet operations apply exact stored targets.

### Known limits

- Live authenticated Jules acceptance remains opt-in.
- Account capacity is estimated from configured limits and observed state; Jules exposes no authoritative quota endpoint.
- Native Jules schedules and issue-label triggers remain external ingress paths.

# Alpha implementation status

`julesctl` has crossed the no-auth control-kernel threshold. The direct Python adapter, local transaction ledger, incremental activity reconciliation, credential-isolated queue, exact fleet controls, machine contracts, and operator runbooks are implemented on `main`.

## Implemented

- direct Jules `v1alpha` REST adapter with exact source resolution;
- complete guarded pagination and forward-compatible response models;
- separate dispatch key, spec fingerprint, request fingerprint, attempt ID, and session ID;
- `RESERVED` versus `SEND_STARTED` transaction fence;
- one automatic create POST per attempt;
- reconciliation after `FAILED_PRECONDITION`, retryable server responses, transport loss, and malformed successful responses;
- pre-send fleet baselines, candidate hydration, strict matching, and unique remote ownership;
- pre-mutation evidence for messages, plan approval, archive, and unarchive;
- persistent incremental activity cursors with overlap and immutable-ID deduplication;
- profile-scoped SQLite state, schema versioning, backups, and integrity checks;
- keyless candidate submission and a credentialed bounded worker with repository allowlists;
- exact deletion plans and keyless local fleet fencing;
- stable JSON/JSONL operation and event envelopes;
- cross-platform Ruff, mypy, test, package-build, and installed-wheel CI gates;
- protected, manual, budgeted live-acceptance workflow.

## Proven without Jules credentials

The automated evidence covers:

- side-effect-then-error session creation without a second POST;
- malformed successful create responses;
- definitive rejection remaining definitive;
- hard freeze before send;
- duplicate work keys and remote-session ownership;
- pagination loops, empty pages, duplicate resources, and additive fields;
- historical message and approval false positives;
- same-timestamp activity overlap and filter fallback;
- profile isolation and database backup/migration;
- queue claim, release, policy rejection, and per-item worker receipts;
- exact deletion targets;
- machine-output schema validation;
- Windows, macOS, and Linux packaging and execution.

## Not yet proven against the live Jules service

A real `JULES_API_KEY` was not available to the implementation campaign. The following remain bounded live-acceptance work:

1. current source names and Discovery revision on the account;
2. one repoless create, observe, and delete lifecycle;
3. one source-backed read-only create, observe, and delete lifecycle;
4. current activity-filter behavior and propagation timing;
5. active-delete and concurrency-release timing;
6. optional `workingBranch`, archive/unarchive, and environment-variable semantics.

The manual `Jules live acceptance` workflow is the canonical path. It requires the protected `jules-live` environment, `JULES_API_KEY`, and an explicit task-start budget. Ordinary CI never consumes Jules quota or mutates a connected repository.

## Release posture

No public release should claim live compatibility until the read-only and lifecycle probes pass. Until then, `main` is the reviewed alpha implementation candidate and `cjules` remains the independent emergency actuator and behavioral comparison target.

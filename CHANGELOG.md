# Changelog

All notable changes to `julesctl` are documented here.

The project follows semantic versioning after `0.1.0`; pre-release contracts may change with an explicit migration note.

## Unreleased

### Added

- Direct Python Jules `v1alpha` REST adapter with exact Source resolution and guarded pagination.
- Stable `JulesClient` facade for Python callers.
- Agent-native `new` command with prompt/file/stdin input, GitHub repository and branch inference, repoless execution, default-branch selection, auto-PR control, plan approval, caller-owned dispatch keys, and bounded parallel attempts.
- Exhaustive `ls`, `show`, `activities`, and restart-safe `watch` commands with local state/repository/age/lifecycle filters.
- One-attempt `msg` and `approve` steering operations with pre-mutation reconciliation evidence.
- Normalized `result`, exact `patch`, and pull-request extraction across session outputs and activity artifacts.
- Preview-first exact-target `prune`, explicit `rm`, bounded settle passes, and explicit fresh-attempt `retry`.
- SQLite-backed work identity, dispatch attempts, session ownership, activity receipts, cursors, deletion plans, profiles, migrations, integrity checks, and backups.
- Keyless local fleet freeze and credential-isolated candidate submission.
- Transactional candidate queue with repository allowlists, expiry checks, bounded worker batches, stale-claim recovery, and per-item receipts.
- Stable JSON/JSONL operation, session, activity, event, dispatch-result, and worker-result contracts.
- Typed wheel metadata through `py.typed`.
- Build-only release receipts with checked wheels, source distributions, and SHA-256 manifests.
- Protected, manual, task-budgeted live acceptance for read-only, repoless, and source-backed lifecycles.
- Architecture decisions, threat model, delegation contract, security policy, release-readiness ledger, and operator runbooks.

### Changed

- Safe reads and DELETE honor bounded retry policy and `Retry-After`; non-idempotent writes remain single-shot and reconcile after uncertain outcomes.
- Session filtering is performed client-side after complete pagination because the documented Sessions endpoint has no filter parameter.
- Activity cursor behavior follows the documented `createTime` surface with overlap and immutable-ID deduplication.
- Supported dependency ranges now include setup-python 7, Rich 15, pytest-cov 7, and mypy 2 after complete CI and security verification.

### Security

- API keys are environment-only.
- HTTP proxy inheritance and redirects are disabled for Jules requests.
- Git subprocesses use a fixed executable, argv-only execution, no shell, and bounded timeouts.
- Destructive fleet operations apply exact stored targets.
- Machine diagnostics redact API keys, credential headers, URL userinfo, and secret-like values.
- Routine result output summarizes media rather than inlining base64 bodies.
- CI enforces branch-aware coverage, dependency auditing, static security analysis, installed-wheel smoke, and an aggregate gate.
- Dependabot tracks Python and GitHub Actions dependencies.

### Known limits

- Authenticated Jules acceptance remains unearned until the protected `jules-live` environment receives a dedicated `JULES_API_KEY`.
- Account capacity is estimated from configured limits and observed state; Jules exposes no authoritative quota endpoint.
- Native Jules schedules, issue-label triggers, the Jules UI, and unrelated clients remain external ingress paths.
- The current repository ruleset prevents deletion and non-fast-forward updates but does not yet require pull requests, the aggregate gate, or conversation resolution.
- No public package or GitHub release should claim live compatibility until the baseline live probes pass.

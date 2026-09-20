# Alpha implementation status

`julesctl` has crossed the no-auth control-plane threshold. The direct Python adapter, stable Python client, agent CLI, local transaction ledger, incremental activity reconciliation, credential-isolated queue, exact fleet controls, machine contracts, release build, security gates, and operator runbooks are implemented on `main`.

## Implemented

- direct Jules `v1alpha` REST adapter with exact Source resolution;
- complete guarded pagination and forward-compatible response models;
- stable `JulesClient` facade;
- agent-native `new`, `ls`, `show`, `activities`, `watch`, `msg`, `approve`, `result`, `patch`, `pr`, `rm`, `prune`, and `retry` commands;
- prompt/file/stdin task packets, GitHub repository and branch inference, repoless execution, default-branch selection, and bounded parallel attempts;
- separate dispatch key, spec fingerprint, request fingerprint, attempt ID, and session ID;
- `RESERVED` versus `SEND_STARTED` transaction fence;
- one automatic create POST per attempt;
- reconciliation after `FAILED_PRECONDITION`, retryable server responses, transport loss, and malformed successful responses;
- pre-send fleet baselines, candidate hydration, strict matching, and unique remote ownership;
- pre-mutation evidence for messages, plan approval, archive, and unarchive;
- persistent incremental activity cursors with overlap and immutable-ID deduplication;
- normalized PR, patch, bash, media-summary, and unknown artifact evidence;
- profile-scoped SQLite state, schema versioning, backups, and integrity checks;
- keyless candidate submission and a credentialed bounded worker with repository allowlists;
- exact deletion plans, bounded concurrent deletes, settle-pass evidence, and keyless local fleet fencing;
- stable JSON/JSONL operation, session, activity, event, dispatch-result, and worker-result envelopes;
- cross-platform Ruff, mypy, test, coverage, package-build, Twine, installed-wheel, Bandit, and dependency-audit gates;
- build-only release workflow with SHA-256 receipts;
- protected, manual, task-budgeted live-acceptance workflow.

## Proven without Jules credentials

The automated evidence covers:

- side-effect-then-error session creation without a second POST;
- malformed successful create responses;
- definitive rejection remaining definitive;
- hard freeze before send;
- duplicate work keys and remote-session ownership;
- pagination loops, empty pages, duplicate resources, and additive fields;
- client-side state, source, age, active, and nonterminal fleet filters;
- historical message and approval false positives;
- same-timestamp activity overlap, restart-safe cursors, terminal settle, and filter fallback;
- PR/patch/artifact normalization, media redaction, selectors, and exact patch stdout;
- lost DELETE responses, same-target `404`, partial deletion, exact plan application, and post-snapshot refill;
- explicit fresh-attempt retry identity rather than replaying an uncertain create;
- profile isolation, database backup/migration, queue claim/release, policy rejection, and stale-claim recovery;
- prompt-source conflicts, symlinks, Git remote forms, detached HEAD, repoless/default-branch rules, and parallel dispatch keys;
- machine-output schema validation, stable error kinds, redaction, and stdout/stderr separation;
- Linux, macOS, and Windows packaging and execution on Python 3.11 and 3.13.

## Live public Discovery evidence

The public Jules Discovery document was fetched and checked from GitHub Actions on 2026-09-20.

```text
revision: 20260918
digest: sha256:f8d6a85bdc47252097a4e279313fcbc5250090ab68e3471be4df2b9e074eb846
compatible: true
missing expected operations: none
unexpected operations: none
```

The live document contained the twelve expected methods:

```text
sources.list
sources.get
sessions.create
sessions.list
sessions.get
sessions.delete
sessions.sendMessage
sessions.approvePlan
sessions.archive
sessions.unarchive
sessions.activities.list
sessions.activities.get
```

This proves that the checked-in operation surface still matches the public service contract. It does not prove authenticated account behavior or the semantics of any mutation.

## Authenticated Jules evidence

Live account compatibility is not yet earned.

A read-only account canary was run through the `jules-live` environment on 2026-09-20. The workflow reached the probe, but `secrets.JULES_API_KEY` resolved to an empty value. The probe failed before making any authenticated Jules request:

```text
Failed: JULES_API_KEY is required for live probes
```

No account state was read or mutated and no task start was consumed. The temporary canary workflow was removed after the receipt was captured.

The remaining live-acceptance work is:

1. add a dedicated `JULES_API_KEY` secret to the protected `jules-live` environment;
2. record the connected Source names and authenticated list/get response shapes through the read-only probe;
3. complete one repoless create, observe, and delete lifecycle;
4. complete one source-backed non-editing create, observe, and delete lifecycle against a disposable connected fixture repository;
5. record activity propagation, response sparsity, and cleanup behavior;
6. only then probe optional `workingBranch`, archive/unarchive, active-delete timing, and environment-variable semantics.

Issue [#13](https://github.com/EffortlessMetrics/julesctl/issues/13) is the durable live-acceptance ledger.

## Repository lock status

The active `main` ruleset prevents branch deletion and non-fast-forward updates. It does not yet require pull requests, the aggregate `gate` check, or conversation resolution. Those settings, repository metadata, protected-environment scope, and private vulnerability reporting are tracked in [#14](https://github.com/EffortlessMetrics/julesctl/issues/14).

## Release posture

No public release should claim live Jules compatibility until the authenticated read-only and two lifecycle probes pass.

Until then, `main` is the fully built and reviewed no-auth alpha candidate. The release workflow may produce installation artifacts and checksums, but publication remains withheld. `cjules` remains the independent emergency actuator and behavioral comparison target.

# Release readiness

This document separates implemented behavior, automated evidence, live public API evidence, authenticated Jules evidence, and GitHub administration. Those are different claims.

## Implementation status

The `0.1.0a1` control surface is implemented:

- direct Jules `v1alpha` REST adapter;
- complete guarded pagination and exact Source resolution;
- agent-native `new`, fleet listing, observation, steering, result collection, deletion, pruning, and retry commands;
- stable Python `JulesClient` facade;
- one-attempt create semantics and ambiguous-write reconciliation;
- durable SQLite work, attempt, session, activity, deletion-plan, profile, and queue state;
- restart-safe incremental activity cursors;
- credential-isolated candidate submission and bounded worker execution;
- stable JSON/JSONL operation, event, session, activity, and worker-result surfaces;
- build-only release workflow with checked wheel, source distribution, and SHA-256 receipts.

## Automated evidence

Every change to `main` is expected to pass:

```text
ruff format --check .
ruff check .
mypy
pytest --cov=julesctl --cov-branch --cov-fail-under=65
Linux × Python 3.11 and 3.13
macOS × Python 3.11 and 3.13
Windows × Python 3.11 and 3.13
python -m build
python -m twine check dist/*
clean installed-wheel CLI smoke
Bandit
pip-audit
aggregate gate
```

The failure-model tests cover, among other cases:

- server-side create followed by `400 FAILED_PRECONDITION`, `5xx`, malformed success, or lost response;
- no automatic second create POST;
- zero, one, and multiple reconciliation candidates;
- hard freeze before network send;
- source/session/activity pagination loops and duplicate resources;
- same-timestamp activity boundaries and filter fallback;
- historical message and plan-approval false positives;
- exact deletion-plan targets, lost DELETE responses, partial failure, and post-snapshot refill;
- profile isolation, database migration backup, queue claim/release, and stale-claim recovery;
- machine-output schemas, stdout/stderr separation, prompt-source conflicts, Git inference, artifact selection, and secret redaction.

## Live public API evidence

The unauthenticated Jules Discovery document was fetched from GitHub Actions on 2026-09-20.

```text
revision: 20260918
digest: sha256:f8d6a85bdc47252097a4e279313fcbc5250090ab68e3471be4df2b9e074eb846
compatible: true
missing expected operations: none
unexpected operations: none
```

All twelve expected Source, Session, and Activity methods were present. This proves that the implemented operation surface still matches the public Discovery contract. It does not prove authenticated account behavior, payload sparsity, propagation timing, quota behavior, or mutation semantics.

## Authenticated Jules evidence

The protected `Jules live acceptance` workflow is implemented, but live account compatibility is not yet earned.

A read-only account canary was run through the `jules-live` environment on 2026-09-20. The job reached the probe normally, but the environment secret resolved to an empty value. It failed before making an authenticated Jules request:

```text
Failed: JULES_API_KEY is required for live probes
```

No Jules state was read or mutated and no task start was consumed.

The remaining prerequisite is external to the repository contents:

1. add a dedicated `JULES_API_KEY` secret to the protected `jules-live` environment;
2. run `read-only` with task budget `0` and record connected Sources plus list/get/activity response shapes;
3. run `repoless` with task budget `1` and verify cleanup;
4. run `source` against a disposable connected fixture repository with task budget `1` and verify cleanup;
5. preserve observed session/activity timing and cleanup receipts;
6. only then probe optional `workingBranch`, archive/unarchive, active-delete timing, and environment-variable behavior.

Issue [#13](https://github.com/EffortlessMetrics/julesctl/issues/13) is the durable live-acceptance ledger.

## Repository administration

The active `main` ruleset currently prevents branch deletion and non-fast-forward updates. It does not yet require pull requests, the aggregate `gate` check, or conversation resolution.

The remaining GitHub-admin actions are:

- require pull requests for `main`;
- require the aggregate `gate` status;
- require conversation resolution;
- retain deletion and force-push protection;
- protect the `jules-live` environment and scope its API key;
- add the repository description and topics;
- confirm private vulnerability reporting.

Issue [#14](https://github.com/EffortlessMetrics/julesctl/issues/14) tracks those settings because they cannot be enforced by repository files alone.

## Release call

Do not publish a package or GitHub release that claims live Jules compatibility until the three authenticated baseline probes pass.

Before that point, `main` is the fully built and reviewed **no-auth alpha candidate**. The build workflow may be run to produce installation artifacts and checksums, but publication remains withheld.

After live acceptance:

1. update `docs/alpha-status.md` with the exact receipts;
2. move the current Unreleased changelog into the chosen alpha version;
3. bump the package version;
4. run the build-only release workflow from the exact tag candidate;
5. verify artifact checksums and clean installation;
6. create the GitHub pre-release;
7. publish to PyPI only if that distribution channel is intended.

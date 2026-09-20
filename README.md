# julesctl

Jules can implement and test bounded repository work, but its UI is a poor fleet-control surface. `julesctl` turns the public Jules REST API into a durable control plane for agents, humans, and automation.

It owns the failure-prone boundary:

```text
candidate packet
    → one journaled create attempt
    → recoverable Jules session
    → durable activities and steering
    → PR, patch, and verification evidence
    → exact fleet cleanup
```

It is not a Python port of `cjules`, a wrapper around the Google Labs TypeScript SDK, a PR reviewer, or a general cloud-agent framework.

## Status

`0.1.0a1` is a reviewed alpha implementation against the Jules `v1alpha` API.

The no-auth contract is exercised across Linux, macOS, and Windows on Python 3.11 and 3.13. CI enforces Ruff, mypy, branch-aware coverage, package build, Twine validation, installed-wheel smoke, Bandit, and `pip-audit`.

Authenticated Jules acceptance remains intentionally separate. The protected live workflow exists, but the `jules-live` environment still needs a dedicated `JULES_API_KEY` before the read-only and lifecycle probes can run. See [`docs/release-readiness.md`](docs/release-readiness.md).

## Install from source

```bash
git clone https://github.com/EffortlessMetrics/julesctl.git
cd julesctl
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Set the key only in the trusted process that talks to Jules:

```bash
export JULES_API_KEY='...'
```

`julesctl` does not accept API keys on argv and does not load repository `.env` files.

## One bounded delegation

From a connected GitHub repository:

```bash
julesctl new \
  --file task.md \
  --auto-pr \
  --dispatch-key github:EffortlessMetrics/perl-lsp:issue:1234:implementation \
  --json
```

`new` can infer the GitHub repository and current branch. Use `--repo`, `--branch`, `--default-branch`, or `--repoless` when inference is not the intended contract.

Prompt sources are deterministic and mutually exclusive:

```bash
julesctl new 'Fix the parser regression' --json
julesctl new --file task.md --json
cat task.md | julesctl new - --json
```

Parallel attempts receive distinct attempt identities:

```bash
julesctl new --file task.md --parallel 3 --dispatch-key experiment:parser --jsonl
```

## Observe and steer

```bash
# Complete account enumeration; filters are applied locally.
julesctl ls --nonterminal --jsonl
julesctl ls --state FAILED --older-than 7d --json
julesctl show SESSION_ID --json

# Immutable activity history and restart-safe incremental watch.
julesctl activities SESSION_ID --jsonl
julesctl watch SESSION_ID --jsonl

# One-attempt steering writes with reconciliation after uncertain responses.
julesctl msg SESSION_ID --file reply.md --json
julesctl approve SESSION_ID --json
```

`watch` emits state, activity, and terminal events. It does not emit heartbeat noise by default and performs a final settled activity read after a terminal state.

## Collect delivery evidence

```bash
julesctl result SESSION_ID --json
julesctl patch SESSION_ID > change.diff
julesctl patch SESSION_ID --index 0 --output change.diff
julesctl pr SESSION_ID --json
```

`result` normalizes session outputs and activity artifacts. Routine output summarizes media rather than inlining base64 bodies. `patch` writes exact unified diff content without Rich formatting.

## Safe fleet control

Explicit session deletion requires confirmation:

```bash
julesctl rm SESSION_ID OTHER_SESSION --yes --json
```

Fleet pruning is preview-first:

```bash
# Create an immutable exact-target plan. No remote writes.
julesctl prune --nonterminal --json
julesctl prune --state FAILED --older-than 7d --json
julesctl prune --all --json

# Apply only the reviewed plan.
julesctl prune --apply PLAN_ID --yes --settle 5 --passes 3 --json
```

The result distinguishes deleted, already absent, failed, and post-snapshot sessions. Unknown future states are excluded from named/nonterminal destructive defaults unless explicitly included.

A prior work object can be attempted again only through an explicit fresh dispatch identity:

```bash
julesctl retry SESSION_ID --dispatch-key retry:issue:1234:2 --json
```

It does not replay an uncertain create attempt.

## Credential-isolated automation

Candidate producers do not need `JULES_API_KEY`:

```bash
julesctl queue submit --spec task.json --json
julesctl queue status --json
```

One trusted worker owns the key and repository policy:

```bash
JULES_API_KEY='...' \
  julesctl worker run-once \
  --max 5 \
  --allow-repo EffortlessMetrics/perl-lsp \
  --jsonl
```

The queue provides caller-owned idempotency, bounded candidate files, expiry checks, transactional claims, stale-claim recovery, repository allowlists, and one durable result per candidate.

## Lower-level control and diagnostics

```bash
julesctl auth check --json
julesctl api check --json
julesctl source list --json
julesctl source resolve OWNER/REPO --json

julesctl dispatch --spec task.json --json
julesctl reconcile --jsonl

julesctl fleet status --json
julesctl fleet freeze --json
julesctl fleet unfreeze --json
julesctl fleet drain --json
julesctl state check --json
```

Local profiles are selected with `JULESCTL_PROFILE`. Non-default profiles receive separate SQLite state databases, schema versions, pre-migration backups, and integrity checks.

## Governing invariants

- A create attempt is automatically sent at most once.
- An uncertain create is reconciled against exhaustive remote state before another attempt is permitted.
- Dispatch key, work fingerprint, request fingerprint, attempt ID, Jules session ID, Git branch, and PR remain distinct identities.
- Sources, Sessions, and Activities fully paginate and detect repeated page tokens.
- Unknown states, events, outputs, and artifacts remain inspectable.
- Activities are deduplicated by immutable resource identity across overlapped cursor reads.
- Message and plan-approval recovery use pre-mutation evidence; historical matches cannot prove a new write.
- Machine stdout is JSON/JSONL only; diagnostics go to stderr.
- Destructive operations apply exact stored targets, not a selector evaluated again later.
- `COMPLETED` means Jules finished one execution epoch. It does not mean the resulting PR is accepted.

## Operating guides

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/threat-model.md`](docs/threat-model.md)
- [`docs/jules-delegation.md`](docs/jules-delegation.md)
- [`docs/release-readiness.md`](docs/release-readiness.md)
- [`docs/runbooks/drain-fleet.md`](docs/runbooks/drain-fleet.md)
- [`docs/runbooks/indeterminate-create.md`](docs/runbooks/indeterminate-create.md)
- [`docs/runbooks/live-acceptance.md`](docs/runbooks/live-acceptance.md)

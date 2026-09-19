# julesctl

`julesctl` is a small Python control kernel for operating Google Jules as delegated asynchronous cloud workers.

It is intentionally **not** a port of `cjules`, a wrapper around the Google Labs TypeScript SDK, or a generic cloud-agent framework. The controller owns the boundary that matters at fleet scale: exact source resolution, caller-owned work identity, one-attempt session creation, reconciliation after uncertain writes, durable activity receipts, and exact fleet deletion plans.

## Status

`0.1.0a1` is an initial control-kernel candidate. The REST API is `v1alpha`; live mutations are opt-in and the implementation preserves unknown server fields and states rather than treating the current schema as closed.

No authenticated Jules lifecycle is implied by unit or fault-simulation results. Live acceptance is a separate, budgeted campaign documented in [`docs/runbooks/live-acceptance.md`](docs/runbooks/live-acceptance.md).

## Install for development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

Set the credential only in the environment of trusted direct operations or the credentialed worker:

```bash
export JULES_API_KEY='...'
```

`julesctl` never accepts an API key as a command-line option and does not load repository `.env` files.

## Core commands

```bash
julesctl auth check --json
julesctl source list --json
julesctl source resolve EffortlessMetrics/perl-lsp --json

julesctl dispatch --spec task.json --json
julesctl session list --all-history --jsonl
julesctl session show SESSION_ID --json
julesctl reconcile --jsonl

julesctl session message SESSION_ID --file reply.md --json
julesctl session approve SESSION_ID --json
julesctl session result SESSION_ID --json

julesctl fleet status --json
julesctl fleet freeze --json
julesctl fleet drain --json
julesctl fleet drain --apply PLAN_ID --yes --json
```

## Credential-isolated automation

Candidate producers do not need `JULES_API_KEY`:

```bash
julesctl queue submit --spec task.json --json
julesctl queue status --json
```

One trusted worker process receives the key and a repository allowlist:

```bash
JULES_API_KEY='...' \
  julesctl worker run-once \
  --max 5 \
  --allow-repo EffortlessMetrics/perl-lsp \
  --jsonl
```

The worker claims a bounded batch transactionally, applies repository policy, and preserves one result per candidate. A frozen fleet prevents new claims and dispatches.

A machine dispatch packet looks like:

```json
{
  "schema": "julesctl.dispatch.v1",
  "dispatch_key": "github:EffortlessMetrics/perl-lsp:issue:1234:implementation",
  "repo": "EffortlessMetrics/perl-lsp",
  "starting_branch": "main",
  "title": "Fix interpolation recovery",
  "prompt": "# Objective\n\nFix ...",
  "auto_create_pr": true,
  "require_plan_approval": false,
  "caller": "codex"
}
```

## Governing invariants

- A create attempt is automatically sent at most once.
- An uncertain create is reconciled against remote state before another attempt is allowed.
- Automated work has a caller-owned `dispatch_key` distinct from request fingerprint, attempt ID, Jules session ID, Git branch, and PR.
- Account-wide claims fully paginate and detect repeated page tokens.
- Unknown states and additive API fields remain inspectable.
- Machine stdout is JSON/JSONL only; diagnostics go to stderr.
- Fleet deletion operates on exact stored target IDs, not a selector re-evaluated later.
- `COMPLETED` means Jules finished an execution epoch; it does not mean the PR is accepted.

## Operating guides

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/threat-model.md`](docs/threat-model.md)
- [`docs/jules-delegation.md`](docs/jules-delegation.md)
- [`docs/runbooks/drain-fleet.md`](docs/runbooks/drain-fleet.md)
- [`docs/runbooks/indeterminate-create.md`](docs/runbooks/indeterminate-create.md)
- [`docs/runbooks/live-acceptance.md`](docs/runbooks/live-acceptance.md)

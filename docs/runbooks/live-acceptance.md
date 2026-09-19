# Live acceptance

Live probes are manual, opt-in, budgeted, and run only after no-auth fault tests pass.

## Preconditions

- Use a dedicated Jules API key and disposable fixture repository.
- Pause native schedules and issue-label triggers.
- Stop unrelated Jules clients and launch loops.
- Enable Reactive Mode.
- Use no production secrets or environment variables.
- Freeze ordinary controller admission during protocol probes.

## Required environment

```text
JULES_LIVE_TEST=1
JULES_API_KEY=...
JULES_TEST_REPO=owner/repo
JULES_TEST_BRANCH=main
JULES_LIVE_TASK_BUDGET=3
```

## Probe sequence

### Read-only

1. Fetch Discovery and record its digest.
2. Authenticate.
3. List and resolve the fixture source.
4. List default and all-history sessions.
5. Read one session and its activities.
6. Record supported filter syntax and error envelopes.

### One source-backed lifecycle

1. Submit one bounded task using only the rendered public create contract.
2. Observe list visibility and direct `GET` fields.
3. Reconcile activities through restart.
4. Collect every PR/output reference.
5. Delete the session and confirm its later absence.

### Optional capabilities

Only after the public-contract lifecycle works, test `workingBranch`, archive/unarchive, activity filtering, and environment-variable control separately.

## Receipts

Record operation and attempt IDs, request hashes, source and branch, session ID, state observations, activity IDs, PR references, cleanup outcome, and Discovery digest. Never record the API key, full private prompts, artifact bodies, or environment values.

A passing mock suite is not live acceptance. A passing live lifecycle is not proof that every uncertain-write failure can be forced on demand.

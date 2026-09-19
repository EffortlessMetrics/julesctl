# Drain the Jules fleet

Use this runbook when Jules capacity is blocked by queued, active, paused, or action-required sessions.

## 1. Freeze local admission

```bash
julesctl fleet freeze --json
```

This fences `julesctl` submissions. It does not stop Jules native schedules, GitHub issue-label triggers, the Jules UI, or other API clients. Pause those separately before interpreting a refill as a deletion failure.

## 2. Inspect the fleet

```bash
julesctl fleet status --json
julesctl session list --jsonl
```

Unknown future states are reported but excluded from the default drain.

## 3. Create an exact plan

```bash
julesctl fleet drain --json > drain-plan-receipt.json
```

Record the returned `plan_id` and target IDs.

## 4. Apply the reviewed plan

```bash
julesctl fleet drain --apply PLAN_ID --yes --json
```

A target already absent is not a fleet-wide failure. Preserve per-target results.

## 5. Settle and rescan

Wait briefly, then list the fleet again. A session created after the plan is new ingress and requires a new plan.

## 6. Reopen admission

```bash
julesctl fleet unfreeze --json
```

Do not unfreeze until unresolved create attempts have been inspected and external ingress is contained.

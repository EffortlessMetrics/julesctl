# Resolve an indeterminate create

An indeterminate attempt means the controller sent one create request but cannot prove whether Jules accepted it. Do not submit the same work again automatically.

## Inspect the attempt

Preserve:

- dispatch key;
- attempt ID;
- request fingerprint;
- original HTTP and Google status;
- candidate session IDs;
- pre-send fleet baseline and send time.

## Inspect remote candidates

```bash
julesctl session list --jsonl
julesctl session show SESSION_ID --json
```

Compare the exact source, starting branch, title, prompt hash, automation settings, creation window, and working branch when that field was sent and returned.

## Adopt one proven match

```bash
julesctl session adopt SESSION_ID --attempt ATTEMPT_ID --json
```

Ordinary adoption refuses a mismatching session. Do not bind a plausible-looking candidate merely to release capacity.

## Zero candidates

Keep the attempt unresolved through the bounded reconciliation window. A zero-result scan is not proof that Jules did not create a delayed session.

## Multiple candidates

Leave the attempt ambiguous and inspect each candidate. Delete only after establishing which sessions are duplicates or unrelated work.

## New attempt

A replacement must be explicit, linked to the original work object, and acknowledge duplicate risk. Never hide the original uncertain attempt by changing its state or reusing its branch.

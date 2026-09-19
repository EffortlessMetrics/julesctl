# Threat model

## Protected assets

- `JULES_API_KEY`
- Jules account start/concurrency budget
- connected repository write authority
- correct work/session ownership
- durable control receipts

## Untrusted inputs

Candidate packets may contain text derived from issues, PR comments, repository documentation, source code, or external web content. Candidate producers do not receive the Jules API key.

## Controls

- credentials come only from `JULES_API_KEY`;
- `httpx` runs with `trust_env=False` and redirects disabled;
- repository `.env` files are never loaded;
- prompts are not persisted in SQLite, only SHA-256 digests;
- request/response bodies are not logged by default;
- `workingBranch` and environment-variable enablement are controller policy, not candidate authority;
- destructive operations require an exact plan plus explicit apply/yes flags;
- unknown remote state is conservative for admission and excluded from destructive defaults.

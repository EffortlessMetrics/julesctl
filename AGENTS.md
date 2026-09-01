# AGENTS.md

## Goal

Build and maintain `julesctl` as a small, evidence-producing control kernel for Google Jules. Keep task selection, architectural judgment, PR review, CI adjudication, and merge outside this repository.

## Hard invariants

1. Never blindly replay a possibly committed `POST /sessions` request.
2. Journal dispatch intent before network mutation.
3. Preserve unknown Jules states, event fields, output fields, and artifacts.
4. Account-wide statements require complete pagination or an explicit `complete=false` result.
5. Do not load API keys from repository files or accept them on argv.
6. Machine stdout must remain valid JSON/JSONL; diagnostics belong on stderr.
7. Destructive fleet operations apply exact stored targets.
8. Do not treat `COMPLETED` as acceptance or merge authority.

## Verification

```bash
ruff format --check .
ruff check .
pytest
python -m build
```

Live Jules tests are opt-in and must never run in ordinary CI.

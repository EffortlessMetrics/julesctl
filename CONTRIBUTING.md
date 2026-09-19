# Contributing

`julesctl` is a narrow control kernel for Google Jules. Keep task discovery, architectural judgment, PR review, CI adjudication, and merge outside this repository.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Required verification:

```bash
ruff format --check .
ruff check .
mypy
pytest --cov=julesctl --cov-branch
python -m build
```

## Change requirements

- Preserve the one-automatic-POST rule for session creation.
- Journal intent before any possibly committed remote mutation.
- Preserve unknown Jules states, activities, outputs, and artifacts.
- Never weaken complete pagination or exact deletion-plan semantics.
- Machine stdout remains valid JSON or JSONL; diagnostics go to stderr.
- Live Jules tests are opt-in, budgeted, and must clean up their sessions.

## Pull requests

Keep one coherent reviewer story. Include the failure mode being controlled, the acceptance boundary, and the exact verification run. Do not include real API keys, full private prompts, patches, shell output, or media in fixtures or logs.

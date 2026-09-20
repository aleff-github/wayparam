# Contributing

## Development setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Before opening a pull request, run:

```bash
ruff check .
ruff format --check .
mypy src/wayparam
pytest -q
```

## Guidelines

- Keep stdout machine-readable (URLs / JSONL); diagnostics belong on stderr.
- Keep the engine frontend-independent: CLI and GUI should build `RunConfig`
  rather than duplicate core behavior.
- Reuse the shared target parser for domain/URL inputs.
- Add tests for parsing, normalization, pagination and error-handling changes.
- Tests must not require outbound network access; use `httpx.MockTransport`
  for HTTP behavior.
- Preserve Python 3.9 compatibility.
- Keep changes focused and document user-visible behavior in the changelog.

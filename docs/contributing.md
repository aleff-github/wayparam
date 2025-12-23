# Contributing

## Development setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -U pip
pip install -e ".[dev]"
```

## Lint
```bash
ruff check .
```

## Tests
```bash
pytest -q
```

## Guidelines
- keep stdout machine-readable (results only)
- diagnostics belong on stderr
- add tests for changes in parsing/normalization
- prefer small, focused pull requests

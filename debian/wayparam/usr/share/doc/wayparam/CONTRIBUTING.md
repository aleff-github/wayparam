# Contributing

## Dev setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -U pip
pip install -e '.[dev]'
pytest
ruff check .
```

## Guidelines
- Keep stdout machine-readable (URLs / JSONL). Put diagnostics on stderr.
- Add unit tests for parsing/normalization changes.

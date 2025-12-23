# Testing

wayparam includes fast, offline tests and httpx-level integration tests.

## Install dev dependencies
```bash
pip install -e ".[dev]"
```

## Run tests
```bash
pytest -q
```

## What is covered

### Unit tests (pure logic)
- URL normalization behavior
- extension parsing and boring URL detection
- resumeKey splitting logic

### Integration tests (httpx MockTransport)
The integration tests simulate the CDX endpoint using `httpx.MockTransport`, so:
- **no real network** is used
- results are deterministic and CI-friendly
- retry and pagination logic can be exercised safely

## Adding new tests
- Prefer pure unit tests for parsing/normalization
- Use MockTransport when testing HTTP behavior
- Keep stdout/stderr separation in mind for CLI tests

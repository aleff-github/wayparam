# Testing

wayparam includes fast, offline tests and httpx-level integration tests.

## Install dev dependencies
```bash
pip install -e ".[dev]"
```

## Run tests
```bash
pytest -q
pytest -q --cov=wayparam --cov-report=term-missing   # with coverage
```

## What is covered

### Unit tests (pure logic)
- URL normalization behavior
- extension parsing and boring URL detection (blacklist and whitelist modes)
- resumeKey splitting logic
- domain-list parsing (`-l`, including `-` for stdin)
- the rate limiter, on a fake clock rather than wall time

### Integration tests (httpx MockTransport)
The integration tests simulate the CDX endpoint using `httpx.MockTransport`, so:
- **no real network** is used
- results are deterministic and CI-friendly
- retry and pagination logic can be exercised safely

### Web UI
The guards (token, `Host` header, unknown paths) run against a server bound to
an ephemeral loopback port. The streamed NDJSON run is tested by replacing
`core.run`, so no run ever touches the network.

## What CI enforces
- `ruff check` and `ruff format --check`
- `mypy src/wayparam`
- the suite on Python 3.9 through 3.14 — `requires-python` is `>=3.9`, so the
  matrix is what actually proves the floor
- a coverage floor (`--cov-fail-under`), meant to ratchet upwards only

## Adding new tests
- Prefer pure unit tests for parsing/normalization
- Use MockTransport when testing HTTP behavior
- Keep stdout/stderr separation in mind for CLI tests
- **Never let a test reach the network**: it makes CI flaky and slow, and the
  Debian build runs the suite in a sandbox with no outbound access

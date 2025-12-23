# Architecture

wayparam is intentionally modular. Each module has a single responsibility, which makes the tool easier to test, audit, and package.

## High-level data flow

1. **cli.py**
   - Parses args
   - Builds option objects
   - Orchestrates concurrency
2. **wayback.py**
   - Builds CDX query parameters
   - Handles pagination/resumeKey
3. **http.py**
   - Makes resilient HTTP requests (retries, backoff)
4. **filters.py**
   - Drops “boring” URLs (static assets) early
5. **normalize.py**
   - Canonicalizes and normalizes URLs (stable output)
6. **output.py**
   - Writes records to files and/or stdout (txt/jsonl)
7. **ratelimit.py**
   - Global RPS limiter (optional)

## Why this structure matters

- unit tests focus on pure logic (`normalize.py`, `filters.py`, parsing)
- integration tests mock HTTP at the transport layer (httpx MockTransport)
- CLI stays pipeline-friendly: stdout is clean and predictable

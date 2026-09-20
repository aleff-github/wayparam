# Architecture

wayparam is intentionally modular. Each module has a single responsibility, which makes the tool easier to test, audit, and package.

## High-level data flow

1. **cli.py / gui/**
   - Parse user input
   - Build a frontend-independent RunConfig
2. **core.py**
   - Orchestrates concurrency, filtering, normalization, deduplication and output
3. **analysis.py**
   - Aggregates uncollapsed capture metadata into endpoint, parameter and domain views
   - Formats deterministic TXT/JSONL historical output
4. **wayback.py**
   - Builds CDX query parameters
   - Handles pagination/resumeKey
   - Exposes both URL-only and capture-metadata iterators
5. **http.py**
   - Makes resilient HTTP requests (retries, backoff)
6. **filters.py**
   - Drops “boring” URLs (static assets) early
7. **normalize.py**
   - Canonicalizes and normalizes URLs (stable output)
8. **output.py**
   - Writes records to files and/or stdout (txt/jsonl)
9. **ratelimit.py**
   - Global RPS limiter (optional)

## Why this structure matters

- unit tests focus on pure logic (`normalize.py`, `filters.py`, parsing)
- integration tests mock HTTP at the transport layer (httpx MockTransport)
- CLI stays pipeline-friendly: stdout is clean and predictable

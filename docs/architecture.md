# Architecture

wayparam is intentionally modular. Each module has a single responsibility, which makes the tool easier to test, audit, and package.

## High-level data flow

1. **cli.py / gui/**
   - Parse user input
   - Build a frontend-independent RunConfig
2. **core.py**
   - Orchestrates concurrency, filtering, normalization, cross-provider deduplication and output
3. **providers/**
   - Defines the archive-provider contract and source records
   - Adapts Wayback to the provider interface
   - Implements Common Crawl collection discovery and CDXJ pagination
4. **analysis.py**
   - Aggregates uncollapsed capture metadata into endpoint, parameter and domain views
   - Formats deterministic TXT/JSONL historical output
5. **wayback.py**
   - Builds CDX query parameters
   - Handles pagination/resumeKey
   - Exposes both URL-only and capture-metadata iterators
6. **http.py**
   - Makes resilient HTTP requests (retries, backoff)
7. **filters.py**
   - Drops “boring” URLs (static assets) early
8. **normalize.py**
   - Canonicalizes and normalizes URLs (stable output)
9. **output.py**
   - Writes records to files and/or stdout (txt/jsonl)
10. **ratelimit.py**
   - Global RPS limiter (optional)

## Why this structure matters

- unit tests focus on pure logic (`normalize.py`, `filters.py`, parsing)
- integration tests mock HTTP at the transport layer (httpx MockTransport)
- providers share one normalization/dedup pipeline, so adding a source does not fork output semantics
- CLI stays pipeline-friendly: stdout is clean and predictable

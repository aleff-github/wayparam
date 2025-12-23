# Internals (deep dive)

This page explains how each module works and how they interact.

---

## `wayparam.cli`

Responsibilities:
- define the public CLI contract (argparse)
- validate option interactions (`--no-files` requires `--stdout`)
- configure concurrency (asyncio semaphore)
- coordinate per-domain processing and aggregate results

### Per-domain processing
For each raw URL returned by CDX:
1) filter boring URLs (`filters.is_boring`)
2) canonicalize (`normalize.canonicalize_url`)
3) filter again (canonicalized URL may reveal a static extension)
4) deduplicate and emit output (`output.write_record` / `output.print_record_stdout`)

---

## `wayparam.wayback`

### Endpoint
The CDX endpoint used is:

- `https://web.archive.org/cdx/search/cdx`

### Paging / resumeKey
CDX may return a resume key at the end of the response. wayparam:
- reads the last line
- detects `resumeKey:` forms and heuristic forms
- loops until no resumeKey (or repeat key safety break)

---

## `wayparam.http`

### Resilience
`get_text()` performs:
- retries on transient errors and HTTP status errors
- exponential backoff
- special handling for 429/503
- includes final `status=...` or `no-status` in the raised error message

This makes troubleshooting much easier in real-world environments (VPNs, flaky networks).

---

## `wayparam.normalize`

Canonicalization steps:
- require absolute URLs with scheme + netloc
- drop fragments
- normalize host casing and default ports
- parse query string, optionally drop tracking params
- optionally replace values with placeholder
- sort params for stable output
- optionally drop URLs with no query params (default behavior)

---

## `wayparam.filters`

Filtering is based primarily on path extension (e.g., `.png`, `.css`, `.js`) and optional regex rules.

Modes:
- blacklist only (default)
- whitelist mode (if `--ext-whitelist` is set)

---

## `wayparam.output`

Key rule:
- keep stdout strictly machine-readable
- send diagnostics to stderr

Formats:
- `txt`: URL per line
- `jsonl`: JSON object per line (record)

---

## `wayparam.ratelimit`

A small async rate limiter that enforces a global RPS limit across all tasks.

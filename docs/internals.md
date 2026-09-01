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

The resumeKey walk itself is faithful, but the server-side `collapse` is not:
while collapsing, the API drops one row at every page boundary. Measured on the
live API, paging one query 18 ways lost 15 of 4684 URLs.

### Block paging (the default)
`showNumPages` + `page`/`pageSize` walks the *index* in fixed blocks instead,
and is lossless with `collapse`. Two things about it are easy to get wrong:

- the page count must be requested **without** `output`/`fl`; with them the
  endpoint answers `-`, which reads as "pagination unavailable" and silently
  sends the run down the lossy path
- the block count follows the size of the domain's index, not the size of the
  filtered result, so a narrow `--from`/`--to` window does not make it cheaper

That second point is why the default is `auto` rather than `blocks`: one
resumeKey-shaped probe settles whether the result even spans pages. If it does
not — the common case — there is no boundary, nothing can be lost, and the run
is done in a single request.

### Memory on a large run
Two things bound a long run, and they were measured rather than guessed
(250 000 unique URLs from one 13 MB block page):

| | peak |
|---|---|
| buffered page + URL strings in the dedup set | 71.1 MB |
| streamed page + URL strings | 30.1 MB |
| streamed page + 16-byte fingerprints | **19.3 MB** |

- **The response body.** Buffering a page costs twice its size — the raw bytes
  and the decoded text — before a single URL reaches the caller. Block pages
  are streamed line by line (`http.iter_lines`), which also means results
  appear while the page is still downloading. The resumeKey walk still buffers:
  finding the cursor means looking at the *last* line, and its pages are capped
  at `--limit` rows anyway.
- **The dedup set.** It holds one entry per emitted URL for the whole domain.
  `core.fingerprint` stores a 16-byte blake2b digest instead of the URL, which
  measured at ~72 bytes per entry against ~148 for the string.

Note the set holds *kept* URLs, not *fetched* rows, and canonicalization
collapses hard — masking values turns thousands of `?id=…` variants into one
entry. Reaching a million entries takes an enormous domain, or `--all-urls`.

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
- whitelist mode (if `--ext-whitelist` is set) — the whitelist replaces the
  blacklist instead of being narrowed by it, so a whitelisted extension is kept
  even when it appears on the default blacklist

Path regex exclusions apply in both modes. A path with no extension is never
judged by extension.

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

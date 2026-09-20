# Changelog

## Unreleased
- Added archive provider abstraction with Wayback remaining the default source
- Added Common Crawl CDXJ URL discovery via `--source commoncrawl`
- Added ordered multi-source collection with `--source wayback,commoncrawl` and cross-provider deduplication
- Added dynamic latest-crawl discovery from Common Crawl `collinfo.json` plus explicit/repeatable `--cc-index`
- Added Common Crawl-specific `--cc-page-size`, `--cc-rps` and `--cc-filter` options
- Common Crawl requests are serialized per run and use a descriptive User-Agent by default
- Added Common Crawl source controls to the optional local web UI
- Historical intelligence remains Wayback-only and now rejects ambiguous multi-archive analysis explicitly
- HTTP helpers can treat provider-specific empty statuses such as Common Crawl 404/no-capture as an empty result

## 0.5.0
- Added capture-level historical intelligence with `--history`, `--params` and `--summary`
- Historical modes request CDX timestamp/status/MIME metadata and disable `collapse=urlkey` so first/last seen and capture counts remain meaningful
- Added deterministic TXT/JSONL analysis output with mode-specific per-domain filenames
- Added the same historical views to the optional local web UI
- Added `--page-size` as the documented alias for `--limit`
- Reject invalid negative/zero CLI numeric settings before a run starts
- Unified domain/URL input normalization across `-d`, `-l` and the web UI
- Fixed IPv6 URL canonicalization and bracketed IPv6 handling in the local web UI
- Percent-encode filename-unsafe target characters so hosts with ports work on Windows
- Corrected the pagination warning to identify `auto` as the default mode
- Added Linux/Windows/macOS CI smoke tests on Python 3.13
- Updated checkout/setup-python workflows to their Node 24-based major versions
- CI now cancels stale runs when a newer commit supersedes the same branch
- Release builds now run `twine check` and smoke-test the built wheel before PyPI upload
- Expanded security reporting and contribution guidance

## 0.4.0
- Added an optional local web interface (`wayparam-gui`), shipped as a separate Debian package and as the `wayparam.gui` snap app
- Added `--version` to the CLI
- Refactored: `RunConfig` + `core.run()` decouple the engine from argparse, so any frontend can drive it
- Fixed thundering-herd retries: HTTP backoff now uses full jitter
- Fixed User-Agent rotating between pages of the same pagination sequence
- `--no-files requires --stdout` is now rejected by argparse, before stdin is consumed
- Errors now name the domain that failed
- Halved URL parsing per candidate (single `is_boring` call, single `urlsplit` inside it)
- Fixed `--ext-whitelist` being cancelled out by the default blacklist (`--ext-whitelist ".png"` used to keep nothing)
- Fixed truncated CDX responses (`httpx.RemoteProtocolError`) escaping the retry loop as a permanent failure
- Fixed `?id=1&id=2` normalizing to `?id=FUZZ&id=FUZZ`; identical parameter pairs now collapse
- The web UI now answers malformed requests instead of dropping the connection (non-object JSON body, non-numeric `rps`/`timeout`)
- An invalid `--exclude-path-regex`, an unreadable `-l` file and an empty domain list are now usage errors (exit 2) instead of tracebacks
- **Fixed silent data loss when a result spans several CDX pages.** The `resumeKey` walk drops one row at each page boundary while `collapse` is on (measured on the live API: 15 of 4684 URLs lost over 18 pages). wayparam now defaults to `--pagination auto`, which probes with a single request and switches to the lossless block API (`showNumPages`/`page`) only when the result really spans pages — the same query now returns all 4684. `--pagination resume` restores the old behaviour and warns; `--pagination blocks` forces the exact walk
- Added `--block-size` (CDX index blocks per request in block mode, default 100)
- Cut peak memory on large runs by 73% (measured: 71.1 MB -> 19.3 MB for 250 000 URLs from one 13 MB page). CDX block pages are now streamed line by line instead of buffered, so results also start appearing while a page is still downloading; and the per-domain dedup set keeps 16-byte fingerprints instead of whole URLs
- No longer waits out `Retry-After` on the final attempt, which was a delay before a certain failure
- Added `--max-results`: a global cap on emitted URLs, so a run can be bounded (`--limit` is only the CDX page size, now also spelled `--page-size`)
- Added a live progress line on stderr, drawn only when stderr is a terminal so pipelines stay clean
- A domain that fails partway through now keeps the stats it earned instead of discarding them; `--stats` marks it `(incomplete)`
- Per-domain output files are flushed while a run is in progress, so a kill no longer loses the buffered tail
- Fixed `RateLimiter` being unconstructible outside a running event loop on Python 3.9 (the lock is now created on first use)
- The web UI no longer prints a traceback when a browser disconnects mid-stream; a closed tab is routine traffic
- CI now runs the suite on Python 3.9-3.14, plus `ruff format --check`, `mypy` and a coverage floor

## 0.3.1
- Fixed BrokenPipeError traceback when the consumer closes the pipe (`--stdout | head`); exits 141 instead
- Added snap packaging (strict confinement)
- Added Debian packaging (PPA, standalone .deb, Debian/Kali)
- Added release workflow for PyPI, Snap Store and GitHub Releases

## 0.3.0
- Milestone

## 0.2.3
- Added httpx-level integration tests using MockTransport (no network)

## 0.2.2
- Fixed pyproject.toml table ordering (project.urls) for editable installs

## 0.2.1
- Added man page (man/wayparam.1)

## 0.2.0
- Added output formats (txt/jsonl) and safer diagnostics to stderr
- Added per-domain stats (optional)
- Improved HTTP error messages with status/no-status
- Added basic tests and CI config

## 0.1.0
- Initial release

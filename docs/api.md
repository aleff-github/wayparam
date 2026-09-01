# Internal API reference (for contributors)

> This is not a public library API. It is provided to help contributors and maintainers.

## `wayparam.config`

### `RunConfig`
The frontend-independent description of one run. Every frontend (CLI, web UI,
embedding code) builds one of these and hands it to `core.run`.

Fields:
- `domains: list[str]`
- `outdir: Path` (default: `results`)
- `write_files: bool` (default: `True`)
- `out_format: "txt" | "jsonl"`
- `max_results: int` (default: `0`, meaning no cap) — global budget across the
  whole run, not per domain
- `concurrency: int`, `rps: float`
- `http: HttpConfig`, `cdx: CdxOptions`, `normalize: NormalizeOptions`,
  `filters: FilterOptions`

### `build_filter_options(ext_blacklist, ext_whitelist, exclude_path_regex) -> FilterOptions`
Turns the textual filter settings every frontend collects into options. A
whitelist replaces the blacklist rather than being narrowed by it.

## `wayparam.core`

### `run(cfg, *, on_record=None, on_progress=None) -> RunResult`
Processes every domain in `cfg`. `on_record` is called for each emitted
`UrlRecord`; `on_progress` is called as `(domain, fetched, kept)` roughly every
1000 fetched rows, so a long run can show progress from inside a single CDX
page.

`BrokenPipeError` is never collected into the result: a closed output means the
consumer went away, which ends the whole run.

### `RunResult`
- `stats: list[DomainStats]`
- `errors: list[tuple[str, Exception]]`
- `ok: bool` — True when `errors` is empty

A domain that fails partway through appears in **both** lists: it keeps the
stats it earned, and its failure is recorded.

### `DomainStats`
- `domain: str`, `fetched: int`, `kept: int`
- `complete: bool` — False when the domain stopped before the CDX stream ran
  out, either because it failed or because the run hit `max_results`

### `fingerprint(url) -> int`
The 128-bit blake2b digest kept in the per-domain dedup set in place of the URL
itself. Deliberately not the built-in `hash()`, which is randomised per process.

### `Budget`
The global `max_results` counter shared by every domain in a run. `take()`
claims one slot and returns False once the cap is reached.

## `wayparam.http`

### `HttpConfig`
Fields:
- `timeout_s: float` (default: 30.0)
- `retries: int` (default: 4)
- `backoff_base_s: float`
- `max_backoff_s: float`
- `user_agent: Optional[str]`
- `proxy: Optional[str]`

### `get_text(client, url, params, config) -> str`
Performs a GET request and returns response text, with retry/backoff behavior.

### `iter_lines(client, url, params, config) -> AsyncIterator[str]`
The same request, streamed: yields non-empty lines as they arrive instead of
buffering the body. Used for CDX block pages, which run to tens of megabytes.
A retry restarts the request — a body cannot be resumed mid-flight — and skips
the lines already delivered, so the caller never sees one twice.

Raises `RuntimeError` after retries with a message like:
- `HTTP request failed after retries (status=503): ...`
- `HTTP request failed after retries (no-status): ...`

## `wayparam.wayback`

### `CdxOptions`
Fields:
- `include_subdomains: bool`
- `collapse: str | None` (default: `urlkey`)
- `from_ts: str | None`
- `to_ts: str | None`
- `limit: int` — CDX rows per request in resumeKey mode
- `filters: list[str] | None`
- `match_type: str | None`
- `pagination: str` (default: `auto`; one of `PAGINATION_MODES`)
- `block_size: int` (default: 100) — index blocks per request in block mode

### `iter_original_urls(domain, client, http_config, rate_limiter, opt) -> AsyncIterator[str]`
Yields “original” URLs from the CDX API, choosing between the two pagination
mechanisms according to `opt.pagination`. See `docs/internals.md` for why the
default (`auto`) probes with one request before committing to the block walk.

### `parse_num_pages(text) -> int | None`
The block count for a query, or `None` when the API answers `-` (pagination
unavailable). Note that the count must be requested without `output`/`fl`, or
the endpoint answers `-` regardless — `_build_num_pages_params` handles this.

## `wayparam.normalize`

### `NormalizeOptions`
Fields:
- `placeholder: str`
- `keep_values: bool`
- `only_params: bool`
- `drop_tracking: bool`
- `drop_empty: bool`
- `sort_params: bool`

### `canonicalize_url(url, opt) -> str | None`
Returns a canonicalized URL or `None` if filtered out or invalid.

## `wayparam.filters`

### `FilterOptions`
Fields:
- `ext_blacklist: set[str]`
- `ext_whitelist: set[str] | None`
- `path_exclude_regex: list[re.Pattern] | None`

### `is_boring(url, opt) -> bool`
Returns True if the URL should be filtered out as “boring”.

## `wayparam.output`

### `UrlRecord`
Fields:
- `domain: str`
- `url: str`
- `source: str` (default: `wayback`)
- `fetched_at: str | None`

### `write_record(fh, rec, fmt)`
Writes one record to a file handle.

### `print_record_stdout(rec, fmt)`
Prints one record to stdout.

### `print_hint_stderr(message)`
Prints diagnostics to stderr.

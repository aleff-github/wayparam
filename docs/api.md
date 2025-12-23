# Internal API reference (for contributors)

> This is not a public library API. It is provided to help contributors and maintainers.

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
- `limit: int`
- `filters: list[str] | None`

### `iter_original_urls(domain, client, http_config, rate_limiter, opt) -> AsyncIterator[str]`
Yields “original” URLs from the CDX API, handling paging/resumeKey.

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

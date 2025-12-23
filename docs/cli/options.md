# CLI — Options (complete reference)

This page documents **every command-line flag** supported by wayparam, including:
- purpose
- default
- interactions and caveats
- practical examples

> Tip: Keep stdout machine-readable (URLs/JSONL) and rely on stderr for diagnostics.

---

## Input

### `-d, --domain DOMAIN`
Query a single domain/host (e.g., `example.com`).

**Default:** none (required unless `--list` is used)

Example:
```bash
wayparam -d example.com
```

### `-l, --list FILE`
Read domains from a file (one per line). Use `-` to read from stdin.

**Default:** none (required unless `--domain` is used)

Supported input normalization:
- strips comments/empty lines
- accepts `https://example.com/path` and extracts host
- lowercases the host

Example:
```bash
wayparam -l domains.txt
```

Example (stdin):
```bash
cat domains.txt | wayparam -l -
```

---

## Output

### `-o, --outdir DIR`
Directory where per-domain files are written.

**Default:** `results`

Example:
```bash
wayparam -d example.com -o out
```

### `--stdout`
Stream results to stdout (machine-readable). Diagnostics remain on stderr.

**Default:** off

Use this for pipelines:
```bash
wayparam -d example.com --stdout --no-files | sort -u
```

### `--format {txt|jsonl}`
Choose output format for stdout and per-domain files.

**Default:** `txt`

- `txt`: one URL per line
- `jsonl`: one JSON object per line (JSON Lines)

Example (JSONL to jq):
```bash
wayparam -d example.com --stdout --no-files --format jsonl | jq -r '.url'
```

### `--no-files`
Disable per-domain files. **Requires `--stdout`.**

**Default:** off

Example:
```bash
wayparam -d example.com --stdout --no-files
```

### `--stats`
Print per-domain counts (`fetched`, `kept`) to **stderr** at the end.

**Default:** off

Example:
```bash
wayparam -l domains.txt --stats
```

### `--quiet`
Only print errors to stderr (useful for scripting).

**Default:** off

Example:
```bash
wayparam -l domains.txt --quiet --stats
```

---

## Wayback / CDX query options

### `--include-subdomains`
Include subdomains by using CDX `matchType=domain` instead of `host`.

**Default:** off (host only)

Example:
```bash
wayparam -d example.com --include-subdomains
```

### `--from TIMESTAMP`
Only include captures from this timestamp/year.

Accepted forms depend on CDX behavior, but commonly:
- `2019`
- `20190101000000`

**Default:** none

Example:
```bash
wayparam -d example.com --from 2019
```

### `--to TIMESTAMP`
Only include captures up to this timestamp/year.

**Default:** none

Example:
```bash
wayparam -d example.com --to 2021
```

### `--no-collapse`
Disable `collapse=urlkey`.

**Default:** collapse enabled (dedup at CDX side)

Disabling collapse may return more duplicates and increase output size:
```bash
wayparam -d example.com --no-collapse
```

### `--filter FILTER`
Pass a CDX filter (repeatable). This is forwarded to the CDX API as-is.

**Default:** none

Common patterns include filtering by status code (depending on CDX capabilities):
```bash
wayparam -d example.com --filter statuscode:200
```

You can repeat:
```bash
wayparam -d example.com --filter statuscode:200 --filter mimetype:text/html
```

### `--limit N`
CDX page size (limit per request).

**Default:** `50000`

Example:
```bash
wayparam -d example.com --limit 10000
```

---

## Normalization options

### `--placeholder STR`
Replace parameter values with this placeholder.

**Default:** `FUZZ`

Example:
```bash
wayparam -d example.com --placeholder X
```

### `--keep-values`
Keep original parameter values.

**Default:** off

⚠️ Not recommended if you share results publicly.

Example:
```bash
wayparam -d example.com --keep-values
```

### `--all-urls`
Include URLs **without** query parameters.

**Default:** off (only parameterized URLs)

Example:
```bash
wayparam -d example.com --all-urls
```

### `--drop-tracking` / `--no-drop-tracking`
Drop common tracking parameters (e.g., `utm_*`, `gclid`, `fbclid`).

**Default:** drop tracking ON (`--drop-tracking`)

Example (keep tracking):
```bash
wayparam -d example.com --no-drop-tracking
```

---

## Filtering options (boring URL filtering)

### `--ext-blacklist CSV`
Comma-separated list of file extensions to exclude. Overrides the default blacklist.

**Default:** built-in blacklist (images, fonts, archives, common static assets, etc.)

Example:
```bash
wayparam -d example.com --ext-blacklist ".png,.jpg,.css,.js"
```

### `--ext-whitelist CSV`
Comma-separated list of extensions to allow. Anything else is excluded.

**Default:** none

Example (only keep `.php` and `.asp`):
```bash
wayparam -d example.com --ext-whitelist ".php,.asp"
```

### `--exclude-path-regex REGEX`
Exclude URLs whose **path** matches this regex. Can be repeated.

**Default:** none

Example:
```bash
wayparam -d example.com --exclude-path-regex "^/static/" --exclude-path-regex "^/assets/"
```

---

## Performance & network options

### `--concurrency N`
Number of domains processed concurrently.

**Default:** `6`

Example:
```bash
wayparam -l domains.txt --concurrency 10
```

### `--rps FLOAT`
Global requests-per-second limit to the Wayback CDX API.

**Default:** `0` (unlimited)

Example (recommended with VPN/proxies):
```bash
wayparam -d example.com --rps 1
```

### `--timeout SECONDS`
HTTP timeout.

**Default:** `30`

Example:
```bash
wayparam -d example.com --timeout 10
```

### `--retries N`
Number of retry attempts on transient failures.

**Default:** `4`

Example:
```bash
wayparam -d example.com --retries 2
```

### `--proxy URL`
Use an HTTP proxy.

**Default:** none

Example:
```bash
wayparam -d example.com --proxy http://127.0.0.1:8080
```

### `--user-agent STR`
Override the User-Agent header.

**Default:** unset (httpx will use its default User-Agent unless overridden)

Example:
```bash
wayparam -d example.com --user-agent "Mozilla/5.0 ..."
```

### `-v, --verbose` (repeatable)
Increase log verbosity. Use:
- `-v` for INFO
- `-vv` for DEBUG

**Default:** warnings/errors only

Example:
```bash
wayparam -d example.com -vv
```

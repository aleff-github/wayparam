# Archive sources

wayparam can collect archived URLs from more than one public web archive index.

The default remains the Internet Archive Wayback CDX API, so existing commands
keep their previous behavior:

```bash
wayparam -d example.com
```

## Select a source

```bash
# Explicit Wayback
wayparam -d example.com --source wayback

# Common Crawl only
wayparam -d example.com --source commoncrawl

# Query both, in this priority order
wayparam -d example.com --source wayback,commoncrawl
```

When several sources are selected, wayparam processes them in the order given
and deduplicates normalized URLs across providers. In JSONL, `source` is the
provider that discovered that normalized URL first.

## Provenance and source overlap

Use provenance mode when the fact that multiple archives observed the same
normalized endpoint is itself useful evidence:

```bash
wayparam -d example.com \
  --source wayback,commoncrawl \
  --provenance --format jsonl --stdout --no-files
```

The default cross-provider deduplication is unchanged. `--provenance` instead
keeps one normalized record per source, while still deduplicating repeated
variants inside each source.

For aggregate coverage intelligence, use:

```bash
wayparam -d example.com \
  --source wayback,commoncrawl \
  --source-summary --format jsonl --stdout --no-files
```

A source summary reports:

- `union_urls`: normalized URLs observed by at least one selected source;
- `source_counts`: unique normalized URLs observed by each source;
- `exclusive_counts`: URLs observed by exactly that source and no other;
- `shared_urls`: URLs observed by at least two selected sources;
- `overlap_urls`: URLs observed by every selected source;
- `complete`: false when a failure or `--max-results` stopped collection early.

`--source-summary` requires at least two sources. Its `--max-results` budget
counts per-source evidence records before aggregation, so bounded results should
not be interpreted as complete archive coverage.

## Common Crawl indexes

Common Crawl publishes separate CDXJ indexes for individual crawls. There is no
single CDXJ index spanning every monthly crawl.

By default, wayparam reads
`https://index.commoncrawl.org/collinfo.json` and uses its newest collection:

```bash
wayparam -d example.com --source commoncrawl
```

For a reproducible query, pin an index:

```bash
wayparam -d example.com \
  --source commoncrawl \
  --cc-index CC-MAIN-2026-39
```

More than one crawl can be queried in one run:

```bash
wayparam -d example.com \
  --source commoncrawl \
  --cc-index CC-MAIN-2026-39 \
  --cc-index CC-MAIN-2026-34
```

Comma-separated index IDs are also accepted.

## Common Crawl pagination

The Common Crawl CDXJ server uses ZipNum block pagination. Its `pageSize`
means compressed index blocks per result page, **not result rows**.

wayparam first asks for `showNumPages=true` and then walks pages from zero to
`pages - 1`:

```bash
wayparam -d example.com --source commoncrawl --cc-page-size 5
```

A smaller page size makes each response lighter. The default is 5.

## Rate limiting and User-Agent

The public Common Crawl index is rate-limited. wayparam therefore:

- serializes Common Crawl requests even when several domains are processed;
- defaults to `--cc-rps 1`;
- sends a descriptive wayparam User-Agent unless `--user-agent` overrides it;
- retries HTTP 429/503 responses using the normal backoff logic.

Common Crawl advises clients to sleep between API calls, avoid simultaneous
threads from one IP, use HTTPS, and avoid proxy networks. If a proxy is
configured together with Common Crawl, wayparam keeps using it but prints a
warning rather than silently changing network behavior.

## Filters and shared scope

These options apply to both providers:

- `--include-subdomains`
- `--from`
- `--to`
- `--no-collapse`
- normalization and local extension/path filters
- `--max-results`

Provider-native CDX filters are separate because field names differ:

```bash
# Wayback
wayparam -d example.com --filter statuscode:200

# Common Crawl
wayparam -d example.com --source commoncrawl --cc-filter status:200
```

## Historical analysis

`--history`, `--params` and `--summary` currently remain Wayback-only.

Historical capture counts from multiple independent archives do not have the
same semantics as capture counts inside one archive, so wayparam rejects a
historical mode when Common Crawl is selected instead of silently producing an
ambiguous aggregate.

## Scope and data access

The Common Crawl provider queries only the CDXJ URL index. It does not download
WARC page bodies and it does not contact the live target.

For bulk corpus analysis, Common Crawl recommends its columnar URL Index rather
than heavily querying the public CDXJ endpoint.

Useful upstream references:

- https://commoncrawl.org/cdxj-index
- https://index.commoncrawl.org/
- https://index.commoncrawl.org/collinfo.json
- https://commoncrawl.org/faq


## Multi-source intelligence

Wayparam 0.7 adds two opt-in views on top of normal multi-source collection.

### Full provenance

`--provenance --format jsonl` preserves one normalized record per selected
archive source. The same normalized endpoint can therefore appear once for
Wayback and once for Common Crawl, while repeated variants inside each source
remain deduplicated. Without `--provenance`, the first configured provider
still wins exactly as before.

### Source overlap summary

`--source-summary` aggregates provider membership per normalized endpoint and
reports, for each domain:

- the union of normalized URLs across configured sources;
- per-source observed URL counts;
- per-source exclusive counts;
- URLs shared by at least two sources;
- URLs observed by every configured source.

These numbers describe evidence present in archive indexes. They do not imply
that an endpoint is currently live, reachable, vulnerable, or still deployed.

The local web interface exposes the same two modes and uses the same core
aggregation logic as the CLI.

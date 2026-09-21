# Historical intelligence

wayparam can aggregate capture-level metadata from the Wayback CDX API instead
of returning only normalized URLs.

The five analysis views are mutually exclusive:

```bash
wayparam -d example.com --history
wayparam -d example.com --params
wayparam -d example.com --summary
wayparam -d example.com --timeline
wayparam -d example.com --changes 2020 2024
```

They work with the normal filtering, date-range, subdomain, proxy, rate-limit
and output options.

## Why analysis mode disables CDX collapse

Normal URL collection uses `collapse=urlkey` by default because only one
representative capture is needed for each archived URL.

Historical statistics are different. First-seen/last-seen dates and capture
counts require every matching capture, so analysis mode automatically queries
CDX without collapse. This can transfer substantially more data than a normal
run.

Use `--from` / `--to`, CDX `--filter` rules, `--rps`, or
`--max-results` to bound the work. In analysis mode, `--max-results` limits
the number of **accepted capture rows** after wayparam's normal filtering and
normalization, not the number of final aggregate records.

## `--history`

One output record is produced for every normalized URL shape.

With the default placeholder behavior, captures such as:

```text
https://example.com/item?id=1&lang=en
https://example.com/item?id=2&lang=en
```

are grouped under:

```text
https://example.com/item?id=FUZZ&lang=FUZZ
```

Each record contains:

- normalized URL
- first capture timestamp
- last capture timestamp
- number of accepted captures
- status-code counts
- MIME-type counts

JSONL is convenient for downstream analysis:

```bash
wayparam -d example.com --history --stdout --no-files --format jsonl
```

## `--params`

This view aggregates by query-parameter name.

Each record contains:

- parameter name
- number of normalized endpoints using it
- total captures represented by those endpoints
- first-seen timestamp
- last-seen timestamp

Example:

```bash
wayparam -d example.com --params --stdout --no-files
```

The text format is tab-separated:

```text
id       14      328     20150203010203  20260819094410
redirect 3       41      20190601000000  20260412115322
```

## `--summary`

This produces one record per domain with:

- raw CDX captures fetched
- accepted captures after filters/normalization
- unique normalized URLs
- unique parameter names
- first/last seen
- status-code distribution
- MIME-type distribution

Example:

```bash
wayparam -d example.com --summary --format jsonl --stdout --no-files
```

## `--timeline`

This view groups accepted historical evidence into deterministic time buckets.
The default is yearly; use `--timeline-granularity month` for month-level output.

Each bucket reports:

- accepted captures in the bucket
- unique normalized URLs observed in the bucket
- normalized URLs whose first accepted capture falls in the bucket
- unique query-parameter names observed in the bucket
- query-parameter names whose first accepted capture falls in the bucket

Example:

```bash
wayparam -d example.com --timeline --timeline-granularity month \
  --format jsonl --stdout --no-files
```

A timeline describes archive-index evidence, not whether an endpoint is still
deployed or reachable today. Date filters such as `--from` and `--to` apply
before the aggregation.

## `--changes BASELINE COMPARISON`

This view compares two archive buckets. Both arguments must use the same form:
either `YYYY` or `YYYYMM`, and the baseline must be earlier than the
comparison period.

The first record is a summary containing baseline/comparison totals and counts
of added, removed and persisted normalized URLs and parameter names. It is
followed by deterministic detail records for each URL and parameter.

Examples:

```bash
wayparam -d example.com --changes 2020 2024 --format jsonl --stdout --no-files
wayparam -d example.com --changes 202401 202501
```

The classifications describe **archive evidence only**:

- `added`: observed in the comparison bucket but not the baseline bucket;
- `removed`: observed in the baseline bucket but not the comparison bucket;
- `persisted`: observed in both buckets.

In particular, `removed` does not establish that an endpoint was deleted from
the live site. Sparse archive coverage can also affect all three categories.
`--from`, `--to` and `--max-results` are applied before the comparison,
so restrictive or bounded runs should be interpreted as partial evidence.

## Output files

When files are enabled, analysis views do not overwrite the normal URL output.
They use mode-specific names:

```text
results/example.com.history.txt
results/example.com.params.txt
results/example.com.summary.txt
results/example.com.timeline.txt
results/example.com.changes.txt
```

With `--format jsonl`, the extension is `.jsonl`.

## Web UI

The local web interface exposes the same four views from the **View** selector:

- Normalized URLs
- Historical endpoints
- Historical parameters
- Domain summary
- Temporal timeline

Historical records are emitted after each domain has been aggregated, while the
normal URL view continues to stream URLs as they are discovered.

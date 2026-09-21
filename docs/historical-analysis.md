# Historical intelligence

wayparam can aggregate capture-level metadata from the Wayback CDX API instead
of returning only normalized URLs.

The individual analysis views are mutually exclusive:

```bash
wayparam -d example.com --history
wayparam -d example.com --params
wayparam -d example.com --summary
wayparam -d example.com --timeline
wayparam -d example.com --changes 2020 2024
wayparam -d example.com --topology
wayparam -d example.com --cooccurrence
wayparam -d example.com --report --format jsonl
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

## `--topology`

This view groups accepted normalized endpoint evidence by **host + path**, ignoring
the URL scheme for grouping while preserving the schemes actually observed.

Each record contains:

- host and path
- observed schemes
- total accepted captures represented by the route
- unique normalized URL variants
- first/last seen timestamps
- union of observed query-parameter names
- deterministic parameter-set variants, each with normalized-URL count,
  represented capture count and first/last seen

Example:

```bash
wayparam -d example.com --topology --format jsonl --stdout --no-files
```

When `--include-subdomains` is enabled, hosts remain separate, so identical paths
on different subdomains are not merged. Parameter-set variants describe archive
observations only and do not imply which parameters are required by a current
live endpoint.

## `--cooccurrence`

This view emits one deterministic record for each unordered pair of query
parameter names observed together on the same normalized endpoint variant.

Each record reports:

- the two parameter names
- number of normalized URL variants containing the pair
- number of distinct host/path routes containing the pair
- represented accepted capture count
- first/last seen timestamps

Example:

```bash
wayparam -d example.com --cooccurrence --format jsonl --stdout --no-files
```

Pairs are descriptive archive-index evidence only. Their presence or frequency
does not imply dependency between parameters, exploitability or current live
behavior.


## `--report`

The evidence report is the versioned, machine-readable bundle intended for
long-lived pipelines and research artifacts. It is JSONL-only and reuses one
historical Wayback metadata pass to emit all compatible views together.

Example:

```bash
wayparam -d example.com --report --format jsonl --stdout --no-files
```

The first line is a `report_manifest` with schema
`wayparam-evidence-report/v1`, the Wayparam generator version, archive source,
domain, evidence scope, included sections, timeline granularity, completeness
state and whether a capture budget was configured.

Following lines are `report_record` wrappers. Each wrapper identifies its
`section` and contains the unchanged record produced by that individual view.
The default sections are:

- `summary`
- `history`
- `params`
- `timeline`
- `topology`
- `cooccurrence`

Optional period-change evidence can be included without a second archive query:

```bash
wayparam -d example.com --report --report-changes 2020 2024 \
  --format jsonl --stdout --no-files
```

Reports are explicitly scoped to archive-index evidence. A bounded or incomplete
run can omit evidence and the manifest records that limitation.


## Output files

When files are enabled, analysis views do not overwrite the normal URL output.
They use mode-specific names:

```text
results/example.com.history.txt
results/example.com.params.txt
results/example.com.summary.txt
results/example.com.timeline.txt
results/example.com.changes.txt
results/example.com.topology.txt
results/example.com.cooccurrence.txt
results/example.com.report.jsonl
```

With `--format jsonl`, the extension is `.jsonl`.

## Web UI

The local web interface exposes the same historical views from the **View** selector:

- Normalized URLs
- Historical endpoints
- Historical parameters
- Domain summary
- Temporal timeline
- Temporal changes
- Surface topology
- Parameter co-occurrence

When **Temporal changes** is selected, enter a baseline and comparison period using
the same `YYYY` or `YYYYMM` rules as `--changes`.

Historical records are emitted after each domain has been aggregated, while the
normal URL view continues to stream URLs as they are discovered.

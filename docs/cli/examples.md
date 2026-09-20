# Practical CLI examples

This page contains practical, copy/paste-friendly examples.

---

## Basic collection

```bash
wayparam -d example.com
```

Writes results to:
- `results/example.com.txt` (default format `txt`)

---

## Multiple domains

```bash
wayparam -l domains.txt
```

---

## Streaming (pipelines)

### Deduplicate and save
```bash
wayparam -d example.com --stdout --no-files | sort -u > urls.txt
```

### Filter by keyword
```bash
wayparam -d example.com --stdout --no-files | grep -i "redirect"
```

### JSONL + jq
```bash
wayparam -d example.com --stdout --no-files --format jsonl | jq -r '.url' | sort -u
```

---

## Subdomains + time filters

```bash
wayparam -d example.com --include-subdomains --from 2018 --to 2021
```

---

## Focus on “dynamic” endpoints

Exclude typical static paths:
```bash
wayparam -d example.com --exclude-path-regex "^/static/" --exclude-path-regex "^/assets/"
```

---

## Being polite to Wayback (recommended)

```bash
wayparam -l domains.txt --rps 1 --concurrency 2
```

---

## Keep parameter values (careful)

```bash
wayparam -d example.com --keep-values
```

Use only if you understand the privacy implications.


---

## Historical endpoint intelligence

Group all accepted captures by normalized URL and retain first/last seen,
capture count, status codes and MIME types:

```bash
wayparam -d example.com --history --stdout --no-files --format jsonl
```

A narrower time window can keep large archives manageable:

```bash
wayparam -d example.com --history --from 2022 --to 2026 --max-results 50000
```

## Historical parameter prevalence

```bash
wayparam -d example.com --params --stdout --no-files
```

The text view is tab-separated and reports parameter name, endpoint count,
capture count and first/last seen timestamps.

## Domain summary

```bash
wayparam -l domains.txt --summary --format jsonl
```

Historical modes automatically disable CDX collapse; expect them to retrieve
more rows than a normal URL collection.

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

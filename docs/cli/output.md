# Output & pipelines

wayparam is designed to be *pipeline-safe*.

## stdout vs stderr

- **stdout**: machine-readable results (URLs or JSONL) **only if** `--stdout` is enabled
- **stderr**: logs, hints, errors, and optional stats

This prevents accidental mixing of results and diagnostics.

## Per-domain files

By default, wayparam writes per-domain files to `results/`:

- `results/<domain>.txt` if `--format txt`
- `results/<domain>.jsonl` if `--format jsonl`

Disable file output with `--no-files` (requires `--stdout`).

## JSONL format

With `--format jsonl`, each output line is a JSON object, for example:

```json
{"domain":"example.com","url":"https://example.com/search?q=FUZZ","source":"wayback","fetched_at":"2025-12-23T12:00:00+00:00"}
```

Common pipeline:
```bash
wayparam -d example.com --stdout --no-files --format jsonl | jq -r '.url'
```

## Stats

`--stats` prints per-domain counts to stderr, so your stdout stream remains pure:

```bash
wayparam -l domains.txt --stdout --no-files --stats | sort -u > urls.txt
```

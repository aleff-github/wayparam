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


## Historical output

Analysis modes use separate filenames so they cannot overwrite normal URL
results:

- `results/<domain>.history.txt` / `.jsonl`
- `results/<domain>.params.txt` / `.jsonl`
- `results/<domain>.summary.txt` / `.jsonl`

The TXT representation is tab-separated. JSONL keeps nested status-code and
MIME-type distributions structured.

Example history record:

```json
{"type":"history","domain":"example.com","url":"https://example.com/item?id=FUZZ","first_seen":"20200101000000","last_seen":"20250101000000","captures":12,"status_codes":{"200":10,"302":2},"mime_types":{"text/html":12}}
```

Example summary record:

```json
{"type":"summary","domain":"example.com","captures":2500,"accepted_captures":720,"unique_urls":180,"unique_parameters":31,"first_seen":"20120101000000","last_seen":"20260801000000","status_codes":{"200":650,"302":70},"mime_types":{"application/json":90,"text/html":630}}
```

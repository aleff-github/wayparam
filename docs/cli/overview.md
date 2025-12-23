# CLI overview

wayparam has a single entry point:

```bash
wayparam [OPTIONS] -d DOMAIN
wayparam [OPTIONS] -l FILE
```

Input is either:
- a single domain/host (`-d/--domain`), or
- a file with one domain per line (`-l/--list`, use `-` for stdin).

## Output modes

wayparam can write:
1) **per-domain files** into `results/` (default), and/or  
2) **stream results to stdout** (`--stdout`) for pipelines.

Diagnostics always go to **stderr**.

## Output formats

- `--format txt` (default): one URL per line
- `--format jsonl`: JSON Lines records with keys:
  - `domain`
  - `url`
  - `source` (currently `wayback`)
  - `fetched_at` (UTC ISO timestamp)

## Exit codes

- `0`: all domains processed successfully
- `2`: partial failure (at least one domain failed)
- `130`: interrupted (Ctrl+C)

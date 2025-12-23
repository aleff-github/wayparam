# Quickstart

## Install (from source)

From the repository root (where `pyproject.toml` lives):

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -U pip
pip install -e .
```

## Run

### Single domain (writes to `results/`)
```bash
wayparam -d example.com
```

### Multiple domains
```bash
wayparam -l domains.txt
```

### Stream to stdout (no files)
```bash
wayparam -d example.com --stdout --no-files
```

### JSONL output (better for automation)
```bash
wayparam -d example.com --stdout --no-files --format jsonl
```

### Be gentle with Wayback (recommended with VPNs)
```bash
wayparam -d example.com --rps 1 --concurrency 2
```

## Notes about output

- **stdout** is reserved for machine-readable results **only when** `--stdout` is enabled.
- logs, hints and optional stats go to **stderr** to keep pipelines clean.

Example safe pipeline:
```bash
wayparam -d example.com --stdout --no-files | sort -u > urls.txt
```

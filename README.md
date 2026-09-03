# wayparam

[![wayparam on the Snap Store](https://snapcraft.io/wayparam/badge.svg)](https://snapcraft.io/wayparam)
[![PyPI](https://img.shields.io/pypi/v/wayparam?color=A81D33)](https://pypi.org/project/wayparam/)
[![BlackArch](https://img.shields.io/badge/BlackArch-available-1793D1?logo=archlinux&logoColor=white)](https://github.com/BlackArch/blackarch/tree/master/packages/wayparam)

**wayparam** is a modern, cross-platform CLI tool to **fetch historical URLs from the Internet Archive Wayback CDX API**, filter out “boring” URLs (static assets), and **normalize query parameters** so you can focus on endpoints that actually matter.

This project is **inspired by ParamSpider** (same overall goal, completely rewritten with a more robust architecture, modern async I/O, better filtering, and production-friendly output behavior).

> OSINT tool: **wayparam does not crawl targets**. It only queries the Wayback CDX API.

Convert this `example.com` into something like this:

```
...
http://www.example.com/_next/image?q=FUZZ&url=FUZZ&w=FUZZ
https://www.example.com/_Incapsula_Resource?SWJIYLWA=FUZZ
http://www.example.com/?format=FUZZ&retailerId=FUZZ
...
```

---

## Key features

- **Wayback CDX API** URL collection (single domain or list)
- **Async + concurrency** for speed on multiple domains
- **Rate limiting** (`--rps`) to be polite with Wayback/CDX
- **Retry + backoff** and clearer error messages
- **CDX pagination** (resumeKey) when available
- Filters “boring” URLs by:
  - extension blacklist/whitelist
  - optional path regex exclusion
- **Canonicalization & normalization**
  - drop fragments
  - normalize host/ports
  - sort parameters
  - mask parameter values (default placeholder: `FUZZ`)
  - optional tracking parameter removal (utm_*, gclid, fbclid, …)
- Output:
  - per-domain files (default)
  - **stdout streaming** for pipelines (`--stdout`)
  - `txt` or `jsonl` output (`--format`)

---

## Installation

### BlackArch Linux

wayparam is officially available in the BlackArch Linux repository.

```bash
sudo pacman -S wayparam
````

On an existing Arch Linux installation with the BlackArch repository enabled, the same package can be installed directly with `pacman`.

### From PyPI (recommended)

```bash
pipx install wayparam     # isolated, keeps the CLI on your PATH
# or
pip install wayparam
```

### Snap (Linux)

```bash
sudo snap install wayparam
```

The snap is strictly confined: it can write only inside your home directory, so
run it from a directory under `$HOME` (or point `-o/--outdir` there). To also
write to mounted media: `sudo snap connect wayparam:removable-media`.

### apt (Debian/Ubuntu/Kali)

A `.deb` is attached to each [GitHub release](https://github.com/aleff-github/wayparam/releases)
and installs the `wayparam(1)` manpage alongside the CLI:

```bash
sudo apt install ./wayparam_<version>_all.deb
```

An apt repository (Launchpad PPA) is not published yet. The Debian packaging
lives in `debian/`; see [Packaging & distribution](docs/packaging-readiness.md)
for how it is built and what it would take to publish one.

### Web interface (optional)

The interface is a separate, opt-in component: the CLI never depends on it.

```bash
sudo apt install wayparam-gui      # separate package; pulls in wayparam
wayparam-gui                       # or: snap run wayparam.gui
```

It prints a URL containing a one-time token and opens it in your browser:

```
wayparam UI: http://127.0.0.1:8765/?t=<token>
```

It binds to **127.0.0.1 only**, requires that token on every request, and
rejects unexpected `Host` headers — it performs outbound requests on behalf of
whoever can reach it, so it is deliberately not reachable from the network. To
use it on a remote box, forward the port instead:

```bash
ssh -L 8765:127.0.0.1:8765 user@host   # then run wayparam-gui there
```

Inside the snap, open the printed URL yourself: a confined snap cannot launch
the host browser.

### From source

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -U pip
pip install -e .
````

### Development install (tests + lint)

```bash
pip install -e ".[dev]"
```

---

## Quick start

### 1) Single domain (writes to `results/`)

```bash
wayparam -d example.com
```

### 2) List of domains

```bash
wayparam -l domains.txt
```

### 3) Stream to stdout (for piping), no files

```bash
wayparam -d example.com --stdout --no-files
```

### 4) JSONL output (great for tooling)

```bash
wayparam -d example.com --stdout --no-files --format jsonl
```

### 5) Include subdomains + be polite to Wayback

```bash
wayparam -d example.com --include-subdomains --rps 1 --concurrency 2
```

### 6) Customize filtering (extensions + path regex)

```bash
wayparam -d example.com --ext-blacklist ".png,.jpg,.css,.js" --exclude-path-regex "^/static/"
```

---

## How it works (under the hood)

1. **Input parsing**

   * `-d/--domain` for a single host
   * `-l/--list` for multiple hosts (one per line, supports comments and basic normalization)

2. **Query the Wayback CDX API**

   * Requests are sent to the CDX endpoint (Wayback Machine)
   * Uses `matchType=host` by default, or `matchType=domain` when `--include-subdomains` is enabled
   * Walks multi-page results losslessly: one probe request, then the block
     pagination API (`showNumPages`/`page`) when the result spans pages, because
     the `resumeKey` walk silently drops one row per boundary while `collapse`
     is enabled

3. **Filter “boring” URLs**

   * Drops URLs that look like static assets (by extension), with optional whitelist mode
   * Optional regex filters can exclude paths (e.g., `/static/`, `/assets/`, …)

4. **Canonicalize + normalize**

   * Removes fragments (`#...`)
   * Normalizes default ports (`:80`, `:443`)
   * Parses query string and:

     * replaces values with a placeholder (default `FUZZ`)
     * optionally drops tracking parameters
     * sorts parameters for stable output
   * Deduplicates results

5. **Output**

   * By default writes per-domain results into `results/`
   * `--stdout` streams machine-readable output
   * Diagnostics (hints, logs, stats) go to **stderr** (safe for pipelines)

---

## Output behavior (important for pipelines)

* **stdout**: only results (URLs or JSONL) when `--stdout` is enabled
* **stderr**: logs, errors, hints (VPN/proxy), optional stats, and a live
  progress line — the progress line is drawn **only when stderr is a terminal**,
  so redirecting or piping stderr stays clean

This means you can safely do:

```bash
wayparam -d example.com --stdout --no-files | sort -u > urls.txt
```

---

## Common options

### Wayback/CDX

* `--include-subdomains`
* `--from 2019` / `--to 2021` (or full timestamps like `20190101000000`)
* `--filter statuscode:200` (repeatable)
* `--no-collapse` (more duplicates, more data)
* `--pagination auto|blocks|resume` — how to walk a multi-page result. The
  default `auto` is **lossless**: the CDX `resumeKey` walk drops one URL at each
  page boundary while `collapse` is on, so wayparam probes with one request and
  switches to the block API only when the result actually spans pages.
* `--block-size 100` (CDX index blocks per request in block mode)

### Normalization

* `--placeholder X`
* `--keep-values` (not recommended if you share logs)
* `--drop-tracking` / `--no-drop-tracking`
* `--all-urls` (include URLs without query parameters)

### Filtering

* `--ext-blacklist ".png,.jpg,.css,.js"`
* `--ext-whitelist ".php,.asp,.aspx"`
* `--exclude-path-regex "regex"` (repeatable)

### Performance / network

* `--max-results 500` (global cap on emitted URLs; `--limit` is only the CDX page size)
* `--concurrency 8`
* `--rps 1` (recommended when using VPNs / noisy networks)
* `--timeout 30`
* `--retries 4`
* `--proxy http://127.0.0.1:8080`

---

## Troubleshooting: VPN / Proxy issues (Wayback CDX)

If you see errors like “failed after retries” against the CDX endpoint, it often means:

* the VPN/proxy exit node is **blocked** or **rate-limited** by Wayback
* your VPN does TLS filtering or networking policies that break automated requests

Try:

* disconnecting VPN/proxy and rerunning
* switching to a different VPN server
* lowering `--concurrency` and setting `--rps 1`

wayparam will print a **human-readable hint in English** to stderr when it detects this pattern.

---

## Man page

A manual page is included:

```bash
man ./man/wayparam.1
```

---

## Testing

Install dev dependencies and run:

```bash
pip install -e ".[dev]"
pytest -q
```

The test suite includes **httpx-level integration tests** using `httpx.MockTransport` (no network).

---

## License

wayparam is **free software** released under the **GNU General Public License v3 (GPLv3)**.
See the `LICENSE` file for details.

---

## Acknowledgements

* Inspired by **ParamSpider** (same objective: fetch Wayback URLs, filter noise, focus on parameterized endpoints).
* Thanks to the OSINT / security community for patterns and workflows around URL collection and parameter discovery.

---

## Disclaimer

Use responsibly and lawfully. This tool queries the Internet Archive and does not actively scan targets, but your downstream usage of collected URLs may have legal and ethical implications depending on context.

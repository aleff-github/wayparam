# wayparam — Full documentation

**wayparam** is a cross-platform Python CLI that queries the Internet Archive **Wayback CDX API** to collect historical URLs for a domain (or a list of domains). It then filters out “boring” URLs (static assets) and **normalizes query parameters** so you can focus on parameterized endpoints that matter.

This project is **inspired by ParamSpider** (same objective, fully rewritten with a modern architecture, async I/O, safer output behavior, and professional tooling).

> **OSINT tool**: wayparam does **not** crawl target websites. It only queries the Wayback Machine CDX API.

## The goal

> “Fetch URLs related to any domain (or list of domains) from the Wayback archives, filter out boring URLs, and focus on the ones that matter most.”

wayparam implements this goal with:
- reliable CDX querying (pagination when supported)
- configurable filtering (extensions + path regex)
- canonicalization and stable, deduplicated output
- pipeline-friendly stdout/stderr separation

## What you get

- Per-domain output files in `results/` by default
- Optional streaming to stdout for chaining tools (sort, grep, jq, etc.)
- Output formats: `txt` or `jsonl`
- Clear diagnostics and hints (e.g., VPN/proxy blocks) **on stderr**

Continue with **Quickstart** to install and run your first command.

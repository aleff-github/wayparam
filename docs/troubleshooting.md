# Troubleshooting

## CDX endpoint fails after retries

If you see errors like:

- `HTTP request failed after retries (status=429): https://web.archive.org/cdx/search/cdx`
- `HTTP request failed after retries (status=403): ...`
- `HTTP request failed after retries (no-status): ...`

Common causes:
- VPN/proxy exit node is blocked or rate-limited by Wayback
- corporate proxy doing TLS inspection
- intermittent connectivity or DNS issues

Recommended actions:
1) disconnect VPN/proxy and retry
2) switch VPN server/region
3) lower concurrency and set rate limit:
   ```bash
   wayparam -d example.com --rps 1 --concurrency 1 -vv
   ```

wayparam prints a human-friendly hint to stderr when it detects this pattern.

## Output looks empty

Possible causes:
- `--all-urls` is NOT set (default is only URLs with query params)
- domain has few archived URLs with query strings
- filtering removes most URLs (extension blacklist/regex too strict)

Try:
```bash
wayparam -d example.com --all-urls
```

or relax filters.

## JSONL output parsing issues

Ensure you are using `--format jsonl`. Each line is a standalone JSON object.

Example:
```bash
wayparam -d example.com --stdout --no-files --format jsonl | jq .
```

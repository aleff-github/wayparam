# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
from pathlib import Path

import httpx

from .filters import DEFAULT_EXT_BLACKLIST, FilterOptions, is_boring, parse_ext_set
from .http import HttpConfig
from .io import ensure_dir, read_domains
from .normalize import NormalizeOptions, canonicalize_url
from .output import (
    UrlRecord,
    now_utc_iso,
    open_outfile,
    print_hint_stderr,
    print_record_stdout,
    write_record,
)
from .ratelimit import RateLimiter
from .wayback import CdxOptions, iter_original_urls

log = logging.getLogger("wayparam")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wayparam",
        description="Fetch and normalize parameterized URLs from the Wayback CDX API.",
    )

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("-d", "--domain", help="Single domain/host (e.g. example.com)")
    src.add_argument("-l", "--list", help="File with domains (one per line). Use '-' for stdin.")

    p.add_argument("-o", "--outdir", default="results", help="Output directory (default: results)")
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Stream results to stdout (machine-readable). Diagnostics stay on stderr.",
    )
    p.add_argument(
        "--format",
        choices=["txt", "jsonl"],
        default="txt",
        help="Output format: txt or jsonl (default: txt)",
    )
    p.add_argument(
        "--no-files", action="store_true", help="Do not write per-domain files (use with --stdout)."
    )
    p.add_argument(
        "--stats", action="store_true", help="Print per-domain stats to stderr at the end."
    )
    p.add_argument("--quiet", action="store_true", help="Only show errors (stderr).")

    # Wayback/CDX options
    p.add_argument(
        "--include-subdomains", action="store_true", help="Include subdomains (matchType=domain)."
    )
    p.add_argument(
        "--from", dest="from_ts", default=None, help="Filter captures from timestamp/year."
    )
    p.add_argument("--to", dest="to_ts", default=None, help="Filter captures to timestamp/year.")
    p.add_argument(
        "--no-collapse", action="store_true", help="Disable collapse=urlkey (more duplicates)."
    )
    p.add_argument(
        "--filter",
        action="append",
        default=None,
        help="CDX filter string (repeatable). Example: statuscode:200",
    )
    p.add_argument("--limit", type=int, default=50000, help="CDX page size (default: 50000).")

    # Normalization/filtering options
    p.add_argument(
        "--placeholder", default="FUZZ", help="Placeholder for parameter values (default: FUZZ)."
    )
    p.add_argument("--keep-values", action="store_true", help="Keep original parameter values.")
    p.add_argument(
        "--all-urls", action="store_true", help="Keep URLs even without query parameters."
    )
    p.add_argument(
        "--drop-tracking",
        action="store_true",
        default=True,
        help="Drop common tracking params (default: on).",
    )
    p.add_argument(
        "--no-drop-tracking",
        action="store_false",
        dest="drop_tracking",
        help="Do not drop tracking params.",
    )

    p.add_argument(
        "--ext-blacklist",
        default=None,
        help="Comma-separated extensions to exclude (overrides defaults).",
    )
    p.add_argument(
        "--ext-whitelist",
        default=None,
        help="Comma-separated extensions to allow; anything else is excluded.",
    )
    p.add_argument(
        "--exclude-path-regex",
        action="append",
        default=None,
        help="Regex to exclude by PATH (repeatable).",
    )

    # Performance/network
    p.add_argument("--concurrency", type=int, default=6, help="Concurrent domains (default: 6).")
    p.add_argument(
        "--rps",
        type=float,
        default=0.0,
        help="Global requests-per-second to Wayback (0 = unlimited).",
    )
    p.add_argument(
        "--timeout", type=float, default=30.0, help="HTTP timeout seconds (default: 30)."
    )
    p.add_argument("--retries", type=int, default=4, help="HTTP retries (default: 4).")
    p.add_argument("--proxy", default=None, help="HTTP proxy URL (e.g. http://127.0.0.1:8080).")
    p.add_argument("--user-agent", default=None, help="Override User-Agent.")
    p.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase log verbosity (-v or -vv)."
    )
    return p


def _setup_logging(verbosity: int, quiet: bool) -> None:
    if quiet:
        level = logging.ERROR
    else:
        level = logging.WARNING
        if verbosity == 1:
            level = logging.INFO
        elif verbosity >= 2:
            level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")


def _maybe_print_wayback_vpn_hint(exc: Exception) -> None:
    msg = str(exc)
    if "web.archive.org/cdx/search/cdx" in msg and "failed after retries" in msg.lower():
        print_hint_stderr(
            "Hint: Requests to the Wayback CDX API failed after multiple retries. "
            "This is often caused by a VPN/proxy exit node being blocked or rate-limited by web.archive.org. "
            "Try disconnecting your VPN/proxy (or switching to a different VPN server), then re-run the same command."
        )


def _asyncclient_kwargs(args: argparse.Namespace, limits: httpx.Limits) -> dict:
    """Support httpx 'proxy' (new) and 'proxies' (old) without pinning versions."""
    kwargs: dict = {"limits": limits, "follow_redirects": True}
    if not args.proxy:
        return kwargs

    try:
        params = inspect.signature(httpx.AsyncClient).parameters
        if "proxy" in params:
            kwargs["proxy"] = args.proxy
        else:
            kwargs["proxies"] = args.proxy
    except Exception:
        # Fallback: try the new name first
        kwargs["proxy"] = args.proxy
    return kwargs


async def _process_domain(
    domain: str,
    *,
    client: httpx.AsyncClient,
    http_cfg: HttpConfig,
    rate_limiter: RateLimiter | None,
    cdx_opt: CdxOptions,
    norm_opt: NormalizeOptions,
    filt_opt: FilterOptions,
    outdir: Path,
    write_files: bool,
    to_stdout: bool,
    out_format: str,
) -> tuple[str, int, int]:
    fetched = 0
    kept = 0
    seen: set[str] = set()

    out_fh = None
    if write_files:
        ext = "jsonl" if out_format == "jsonl" else "txt"
        out_fh = open_outfile(outdir / f"{domain}.{ext}")

    try:
        async for raw in iter_original_urls(
            domain,
            client=client,
            http_config=http_cfg,
            rate_limiter=rate_limiter,
            opt=cdx_opt,
        ):
            fetched += 1

            if is_boring(raw, filt_opt):
                continue

            canon = canonicalize_url(raw, norm_opt)
            if canon is None:
                continue

            if is_boring(canon, filt_opt):
                continue

            if canon in seen:
                continue
            seen.add(canon)

            kept += 1
            rec = UrlRecord(domain=domain, url=canon, fetched_at=now_utc_iso())

            if out_fh:
                write_record(out_fh, rec, out_format)  # type: ignore[arg-type]
            if to_stdout:
                print_record_stdout(rec, out_format)  # type: ignore[arg-type]

    finally:
        if out_fh:
            out_fh.close()

    return domain, fetched, kept


async def run_async(args: argparse.Namespace) -> int:
    # Input
    domains = [args.domain.strip().lower()] if args.domain else read_domains(args.list)

    # Output
    outdir = Path(args.outdir)
    write_files = not args.no_files
    if write_files:
        ensure_dir(outdir)

    if args.no_files and not args.stdout:
        raise SystemExit("--no-files requires --stdout")

    # Filters
    if args.ext_blacklist:
        ext_blacklist = parse_ext_set(args.ext_blacklist)
    else:
        ext_blacklist = set(DEFAULT_EXT_BLACKLIST)

    ext_whitelist = parse_ext_set(args.ext_whitelist) if args.ext_whitelist else None

    import re as _re

    path_rx = [_re.compile(x) for x in (args.exclude_path_regex or [])] or None
    filt_opt = FilterOptions(
        ext_blacklist=ext_blacklist, ext_whitelist=ext_whitelist, path_exclude_regex=path_rx
    )

    # Normalization
    norm_opt = NormalizeOptions(
        placeholder=args.placeholder,
        keep_values=args.keep_values,
        only_params=(not args.all_urls),
        drop_tracking=args.drop_tracking,
    )

    # CDX options
    cdx_opt = CdxOptions(
        include_subdomains=args.include_subdomains,
        collapse=None if args.no_collapse else "urlkey",
        from_ts=args.from_ts,
        to_ts=args.to_ts,
        limit=args.limit,
        filters=args.filter,
    )

    # HTTP config
    http_cfg = HttpConfig(
        timeout_s=args.timeout,
        retries=args.retries,
        user_agent=args.user_agent,
        proxy=args.proxy,
    )

    limits = httpx.Limits(
        max_connections=max(10, args.concurrency * 4),
        max_keepalive_connections=max(10, args.concurrency * 2),
    )

    rate_limiter = RateLimiter(args.rps) if args.rps and args.rps > 0 else None
    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def guarded(d: str):
        async with sem:
            return await _process_domain(
                d,
                client=client,
                http_cfg=http_cfg,
                rate_limiter=rate_limiter,
                cdx_opt=cdx_opt,
                norm_opt=norm_opt,
                filt_opt=filt_opt,
                outdir=outdir,
                write_files=write_files,
                to_stdout=args.stdout,
                out_format=args.format,
            )

    async with httpx.AsyncClient(**_asyncclient_kwargs(args, limits)) as client:
        tasks = [asyncio.create_task(guarded(d)) for d in domains]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    ok = 0
    domain_stats: list[tuple[str, int, int]] = []

    for r in results:
        if isinstance(r, Exception):
            log.error("Error: %s", r)
            _maybe_print_wayback_vpn_hint(r)
            continue
        domain, fetched, kept = r
        ok += 1
        domain_stats.append((domain, fetched, kept))
        log.info("%s: fetched=%d kept=%d", domain, fetched, kept)

    if args.stats:
        for d, fetched, kept in domain_stats:
            print_hint_stderr(f"Stats: {d}: fetched={fetched} kept={kept}")

    return 0 if ok == len(domains) else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.quiet)

    try:
        return asyncio.run(run_async(args))
    except KeyboardInterrupt:
        return 130

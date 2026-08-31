# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import RunConfig, build_filter_options
from .core import RunResult, run
from .http import HttpConfig
from .io import read_domains
from .normalize import NormalizeOptions
from .output import UrlRecord, print_hint_stderr, print_record_stdout
from .wayback import CdxOptions

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
    p.add_argument("--version", action="version", version=f"wayparam {__version__}")
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


def build_config(args: argparse.Namespace) -> RunConfig:
    """Translate parsed CLI arguments into a frontend-independent RunConfig."""
    domains = [args.domain.strip().lower()] if args.domain else read_domains(args.list)

    return RunConfig(
        domains=domains,
        outdir=Path(args.outdir),
        write_files=not args.no_files,
        out_format=args.format,
        concurrency=args.concurrency,
        rps=args.rps,
        http=HttpConfig(
            timeout_s=args.timeout,
            retries=args.retries,
            user_agent=args.user_agent,
            proxy=args.proxy,
        ),
        cdx=CdxOptions(
            include_subdomains=args.include_subdomains,
            collapse=None if args.no_collapse else "urlkey",
            from_ts=args.from_ts,
            to_ts=args.to_ts,
            limit=args.limit,
            filters=args.filter,
        ),
        normalize=NormalizeOptions(
            placeholder=args.placeholder,
            keep_values=args.keep_values,
            only_params=(not args.all_urls),
            drop_tracking=args.drop_tracking,
        ),
        filters=build_filter_options(
            ext_blacklist=args.ext_blacklist,
            ext_whitelist=args.ext_whitelist,
            exclude_path_regex=args.exclude_path_regex,
        ),
    )


def _report(result: RunResult, cfg: RunConfig, show_stats: bool) -> int:
    for domain, exc in result.errors:
        log.error("%s: %s", domain, exc)
        _maybe_print_wayback_vpn_hint(exc)

    if show_stats:
        for st in result.stats:
            print_hint_stderr(f"Stats: {st.domain}: fetched={st.fetched} kept={st.kept}")

    return 0 if len(result.stats) == len(cfg.domains) else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.no_files and not args.stdout:
        parser.error("--no-files requires --stdout")

    _setup_logging(args.verbose, args.quiet)

    try:
        cfg = build_config(args)
        on_record = None
        if args.stdout:

            def on_record(rec: UrlRecord) -> None:
                print_record_stdout(rec, cfg.out_format)

        result = asyncio.run(run(cfg, on_record=on_record))
        return _report(result, cfg, args.stats)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # Downstream closed the pipe (e.g. `wayparam -d x --stdout | head`).
        # Redirect stdout to devnull so the interpreter's flush-on-exit does
        # not raise a second BrokenPipeError, then exit like a Unix filter
        # killed by SIGPIPE (128 + 13). See the note on SIGPIPE in the
        # Python docs for the signal module.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            # stdout has no real file descriptor (captured/embedded): nothing
            # to silence, and nothing to fail over.
            pass
        return 141

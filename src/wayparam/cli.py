# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import re
import sys
from pathlib import Path

from . import __version__
from .analysis import (
    HistoryRunResult,
    format_record,
    records_for,
    run_history,
    write_analysis_files,
)
from .config import RunConfig, build_filter_options
from .core import RunResult, run
from .http import HttpConfig
from .io import normalize_domain, read_domains
from .normalize import NormalizeOptions
from .output import UrlRecord, print_hint_stderr, print_record_stdout
from .providers import CommonCrawlOptions, SourceName, parse_source_names
from .wayback import PAGINATION_MODES, CdxOptions

log = logging.getLogger("wayparam")


def _source_list(value: str) -> tuple[SourceName, ...]:
    try:
        return parse_source_names(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _cc_indexes(values: list[str] | None) -> tuple[str, ...]:
    if not values:
        return ("latest",)
    out: list[str] = []
    for value in values:
        for raw in value.split(","):
            index = raw.strip()
            if index and index not in out:
                out.append(index)
    return tuple(out or ["latest"])


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return parsed


def _nonnegative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite number 0 or greater")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wayparam",
        description="Fetch and normalize parameterized URLs from web archive indexes.",
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

    p.add_argument(
        "--source",
        type=_source_list,
        default=("wayback",),
        metavar="NAME[,NAME...]",
        help="Archive source(s) in priority order: wayback, commoncrawl "
        "(default: wayback). Combined sources are deduplicated.",
    )
    p.add_argument(
        "--provenance",
        action="store_true",
        help="Preserve one normalized JSONL record per archive source instead of "
        "deduplicating the same endpoint across sources.",
    )

    analysis = p.add_mutually_exclusive_group()
    analysis.add_argument(
        "--history",
        dest="analysis",
        action="store_const",
        const="history",
        help="Aggregate capture history per normalized URL (first/last seen, counts, status/MIME).",
    )
    analysis.add_argument(
        "--params",
        dest="analysis",
        action="store_const",
        const="params",
        help="Aggregate historical prevalence per query-parameter name.",
    )
    analysis.add_argument(
        "--summary",
        dest="analysis",
        action="store_const",
        const="summary",
        help="Emit one historical summary record per domain.",
    )

    # Shared archive query options
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
        help="Wayback CDX filter string (repeatable). Example: statuscode:200",
    )
    p.add_argument(
        "--limit",
        "--page-size",
        dest="limit",
        type=_positive_int,
        default=50000,
        help="CDX rows per request in resumeKey mode -- not a cap on results "
        "(default: 50000). Use --max-results to bound a run.",
    )
    p.add_argument(
        "--pagination",
        choices=list(PAGINATION_MODES),
        default="auto",
        help="How to walk multi-page results: 'auto' probes with one request and "
        "switches to the block API only when the result spans pages; 'blocks' always "
        "uses it; 'resume' uses resumeKey paging, which drops one URL per page "
        "boundary while collapse is on (default: auto).",
    )
    p.add_argument(
        "--block-size",
        type=_positive_int,
        default=100,
        help="CDX index blocks per request in block mode (default: 100). Larger means "
        "fewer but slower and heavier responses.",
    )
    p.add_argument(
        "--max-results",
        type=_nonnegative_int,
        default=0,
        help="Stop after this many emitted URLs; in analysis modes, accepted captures "
        "(0 = no cap).",
    )

    # Common Crawl options
    p.add_argument(
        "--cc-index",
        action="append",
        default=None,
        metavar="ID",
        help="Common Crawl index ID (repeatable or comma-separated). "
        "Use 'latest' for the newest crawl (default: latest).",
    )
    p.add_argument(
        "--cc-page-size",
        type=_positive_int,
        default=5,
        help="Common Crawl compressed index blocks per page (default: 5).",
    )
    p.add_argument(
        "--cc-rps",
        type=_nonnegative_float,
        default=1.0,
        help="Common Crawl index requests per second (default: 1).",
    )
    p.add_argument(
        "--cc-filter",
        action="append",
        default=None,
        help="Common Crawl CDXJ filter string (repeatable). Example: status:200",
    )

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
    p.add_argument(
        "--concurrency",
        type=_positive_int,
        default=6,
        help="Concurrent domains (default: 6).",
    )
    p.add_argument(
        "--rps",
        type=_nonnegative_float,
        default=0.0,
        help="Global requests-per-second to Wayback (0 = unlimited).",
    )
    p.add_argument(
        "--timeout",
        type=_positive_float,
        default=30.0,
        help="HTTP timeout seconds (default: 30).",
    )
    p.add_argument("--retries", type=_nonnegative_int, default=4, help="HTTP retries (default: 4).")
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


class _ProgressLine:
    """A single self-overwriting status line on stderr.

    Only used on a terminal: in a pipeline the carriage returns would be
    noise, and stderr is where a `2>` redirect collects real diagnostics.
    """

    def __init__(self) -> None:
        self._per_domain: dict[str, tuple[int, int]] = {}
        self._drawn = False

    def __call__(self, domain: str, fetched: int, kept: int) -> None:
        self._per_domain[domain] = (fetched, kept)
        total_fetched = sum(f for f, _ in self._per_domain.values())
        total_kept = sum(k for _, k in self._per_domain.values())
        label = domain if len(self._per_domain) == 1 else f"{len(self._per_domain)} domains"
        print(
            f"\r{label}: fetched={total_fetched} kept={total_kept}\x1b[K",
            end="",
            file=sys.stderr,
            flush=True,
        )
        self._drawn = True

    def clear(self) -> None:
        if self._drawn:
            print("\r\x1b[K", end="", file=sys.stderr, flush=True)
            self._drawn = False


def _make_progress(quiet: bool) -> _ProgressLine | None:
    if quiet or not sys.stderr.isatty():
        return None
    return _ProgressLine()


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
    if args.domain:
        domain = normalize_domain(args.domain)
        domains = [domain] if domain else []
    else:
        domains = read_domains(args.list)

    return RunConfig(
        domains=domains,
        sources=args.source,
        outdir=Path(args.outdir),
        write_files=not args.no_files,
        out_format=args.format,
        analysis=args.analysis,
        provenance=args.provenance,
        max_results=max(0, args.max_results),
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
            pagination=args.pagination,
            block_size=max(1, args.block_size),
        ),
        commoncrawl=CommonCrawlOptions(
            indexes=_cc_indexes(args.cc_index),
            page_size=args.cc_page_size,
            rps=args.cc_rps,
            filters=tuple(args.cc_filter or ()),
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


def _report(result: RunResult | HistoryRunResult, cfg: RunConfig, show_stats: bool) -> int:
    for domain, exc in result.errors:
        log.error("%s: %s", domain, exc)
        _maybe_print_wayback_vpn_hint(exc)

    if show_stats:
        for st in result.stats:
            suffix = "" if st.complete else " (incomplete)"
            print_hint_stderr(f"Stats: {st.domain}: fetched={st.fetched} kept={st.kept}{suffix}")

    # A failing domain now still reports the stats it produced, so the exit
    # code has to come from the errors rather than from a count of stats.
    return 0 if result.ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.no_files and not args.stdout:
        parser.error("--no-files requires --stdout")
    if args.provenance and args.format != "jsonl":
        parser.error("--provenance requires --format jsonl")
    if args.provenance and args.analysis:
        parser.error("--provenance cannot be combined with --history/--params/--summary")

    _setup_logging(args.verbose, args.quiet)

    # Bad input is a usage error, not a crash: argparse exits 2 with a one-line
    # message instead of unwinding a traceback at the user.
    try:
        cfg = build_config(args)
    except re.error as exc:
        parser.error(f"invalid --exclude-path-regex: {exc}")
    except OSError as exc:
        parser.error(f"cannot read the domain list: {exc}")

    if not cfg.domains:
        parser.error("no domains to process")

    if cfg.analysis and cfg.sources != ("wayback",):
        parser.error("--history/--params/--summary currently require --source wayback")

    if "commoncrawl" in cfg.sources and cfg.http.proxy:
        log.warning(
            "Common Crawl advises against proxy networks for its public index API; "
            "the configured proxy will still be used."
        )
    if "commoncrawl" in cfg.sources and cfg.commoncrawl.rps <= 0:
        log.warning(
            "Common Crawl recommends sleeping between index API calls; "
            "--cc-rps 0 disables that protection."
        )

    progress = _make_progress(args.quiet)
    try:
        on_record = None
        if args.stdout:

            def on_record(rec: UrlRecord) -> None:
                print_record_stdout(rec, cfg.out_format)

        if cfg.analysis:
            history_result = asyncio.run(run_history(cfg, on_progress=progress))
            if progress:
                progress.clear()

            write_analysis_files(history_result, cfg, cfg.analysis)
            if args.stdout:
                for domain in cfg.domains:
                    history = history_result.analyses.get(domain)
                    if history is None:
                        continue
                    for record in records_for(history, cfg.analysis):
                        print(format_record(record, cfg.out_format), flush=True)
            return _report(history_result, cfg, args.stats)

        url_result = asyncio.run(run(cfg, on_record=on_record, on_progress=progress))
        # Erase the status line before anything else writes to stderr, or the
        # report lands on the same line as the last progress update.
        if progress:
            progress.clear()
        return _report(url_result, cfg, args.stats)
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
    finally:
        # However main() leaves, the terminal must not keep a half-drawn line.
        if progress:
            progress.clear()

# SPDX-License-Identifier: GPL-3.0

"""A loopback-only web UI for wayparam.

The page is served from the same process that runs the search, and results are
streamed to the browser as newline-delimited JSON while they are produced.

Security posture: this server can make outbound network requests on behalf of
whoever reaches it, so it binds to the loopback interface only, requires a
per-process random token on every request, and rejects requests whose Host
header is not the address it is listening on (DNS rebinding). Without the
token check, any page open in the user's browser could drive it.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import secrets
import socket
import sys
import threading
from pathlib import Path
from typing import Optional, cast
from urllib.parse import parse_qs, urlsplit

from .. import __version__
from ..analysis import format_record, records_for, run_history, write_analysis_files
from ..config import AnalysisMode, RunConfig, TimelineGranularity, build_filter_options
from ..core import run
from ..http import HttpConfig
from ..io import parse_domains
from ..normalize import NormalizeOptions
from ..output import UrlRecord
from ..providers import CommonCrawlOptions, parse_source_names
from ..source_analysis import (
    format_source_summary,
    run_source_summary,
    write_source_summary_files,
)
from ..wayback import CdxOptions

log = logging.getLogger("wayparam.gui")

_INDEX = Path(__file__).with_name("index.html")
MAX_BODY_BYTES = 256 * 1024


class Rejected(Exception):
    """Request refused before any work was started."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def config_from_request(data: dict) -> RunConfig:
    """Build a RunConfig from the JSON the page submits.

    Every field is optional and falls back to the same defaults as the CLI.
    """
    raw_domains = str(data.get("domains", ""))
    domains = parse_domains(raw_domains.replace(",", "\n").splitlines())
    if not domains:
        raise Rejected(400, "No domain given.")

    try:
        sources = parse_source_names(str(data.get("source", "wayback")))
    except ValueError as exc:
        raise Rejected(400, str(exc)) from None

    outdir = str(data.get("outdir", "") or "").strip()
    write_files = bool(outdir)
    raw_analysis = str(data.get("analysis", "") or "").strip()
    analysis = cast(
        Optional[AnalysisMode],
        raw_analysis if raw_analysis in ("history", "params", "summary", "timeline") else None,
    )
    if analysis and sources != ("wayback",):
        raise Rejected(400, "Historical analysis currently supports Wayback only.")

    raw_timeline_granularity = str(data.get("timeline_granularity", "year") or "year").strip()
    timeline_granularity = cast(
        TimelineGranularity,
        raw_timeline_granularity if raw_timeline_granularity in ("year", "month") else "year",
    )

    provenance = bool(data.get("provenance"))
    source_summary = bool(data.get("source_summary"))
    if provenance and data.get("format") != "jsonl":
        raise Rejected(400, "Provenance requires JSONL output.")
    if provenance and source_summary:
        raise Rejected(400, "Provenance and source summary are mutually exclusive.")
    if provenance and analysis:
        raise Rejected(400, "Provenance cannot be combined with historical analysis.")
    if source_summary and analysis:
        raise Rejected(400, "Source summary cannot be combined with historical analysis.")
    if source_summary and len(sources) < 2:
        raise Rejected(400, "Source summary requires at least two archive sources.")

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(data.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _float(key: str, default: float, lo: float, hi: float) -> float:
        raw = data.get(key)
        if raw is None or raw == "":
            return default
        try:
            return max(lo, min(hi, float(raw)))
        except (TypeError, ValueError):
            return default

    return RunConfig(
        domains=domains,
        sources=sources,
        outdir=Path(outdir) if write_files else Path("results"),
        write_files=write_files,
        out_format="jsonl" if data.get("format") == "jsonl" else "txt",
        analysis=analysis,
        timeline_granularity=timeline_granularity,
        provenance=provenance,
        source_summary=source_summary,
        concurrency=_int("concurrency", 6, 1, 64),
        rps=_float("rps", 0.0, 0.0, 1000.0),
        http=HttpConfig(
            timeout_s=_float("timeout", 30.0, 1.0, 600.0),
            retries=_int("retries", 4, 0, 10),
            proxy=(str(data.get("proxy") or "").strip() or None),
        ),
        cdx=CdxOptions(
            include_subdomains=bool(data.get("include_subdomains")),
            collapse=None if data.get("no_collapse") else "urlkey",
            from_ts=(str(data.get("from_ts") or "").strip() or None),
            to_ts=(str(data.get("to_ts") or "").strip() or None),
            limit=_int("limit", 50000, 1, 200000),
        ),
        commoncrawl=CommonCrawlOptions(
            indexes=tuple(
                item.strip()
                for item in str(data.get("cc_index", "latest")).split(",")
                if item.strip()
            )
            or ("latest",),
            page_size=_int("cc_page_size", 5, 1, 100),
            rps=_float("cc_rps", 1.0, 0.0, 100.0),
        ),
        normalize=NormalizeOptions(
            placeholder=str(data.get("placeholder") or "FUZZ"),
            keep_values=bool(data.get("keep_values")),
            only_params=not bool(data.get("all_urls")),
            drop_tracking=data.get("drop_tracking", True) is not False,
        ),
        filters=build_filter_options(
            ext_blacklist=(str(data.get("ext_blacklist") or "").strip() or None),
            ext_whitelist=(str(data.get("ext_whitelist") or "").strip() or None),
        ),
    )


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"wayparam/{__version__}"

    # Set by serve()
    token: str = ""
    allowed_hosts: frozenset[str] = frozenset()

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ---- guards -------------------------------------------------------

    def _check_host(self) -> None:
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in self.allowed_hosts:
            raise Rejected(421, "Unexpected Host header.")

    def _check_token(self, supplied: str | None) -> None:
        if not supplied or not secrets.compare_digest(supplied, self.token):
            raise Rejected(403, "Missing or invalid token.")

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Rejected(400, "Bad Content-Length.") from None
        if length <= 0 or length > MAX_BODY_BYTES:
            raise Rejected(413, "Request body missing or too large.")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Rejected(400, "Body is not valid JSON.") from None
        if not isinstance(data, dict):
            raise Rejected(400, "Body must be a JSON object.")
        return data

    # ---- responses ----------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        if status >= 400:
            # A rejected POST leaves its body unread on the socket; reusing the
            # connection would then parse those bytes as the next request.
            self.close_connection = True
        self.send_response(status)
        if status >= 400:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The page never embeds remote resources and is never framed.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, exc: Rejected) -> None:
        self._send(
            exc.status,
            json.dumps({"error": exc.message}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    # ---- routes -------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        try:
            self._check_host()
            path = urlsplit(self.path)
            if path.path != "/":
                raise Rejected(404, "Not found.")
            supplied = parse_qs(path.query).get("t", [""])[0]
            self._check_token(supplied or None)
            self._send(200, _INDEX.read_bytes(), "text/html; charset=utf-8")
        except Rejected as e:
            self._send_error_json(e)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._check_host()
            if urlsplit(self.path).path != "/api/run":
                raise Rejected(404, "Not found.")
            self._check_token(self.headers.get("X-Wayparam-Token"))
            cfg = config_from_request(self._read_json())
        except Rejected as e:
            self._send_error_json(e)
            return
        except Exception:  # noqa: BLE001 - answer, never drop the connection
            log.exception("could not build a run from the request")
            self._send_error_json(Rejected(400, "Could not build a run from that request."))
            return

        self._stream_run(cfg)

    # ---- the run itself -----------------------------------------------

    def _stream_run(self, cfg: RunConfig) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        def chunk(obj: dict) -> None:
            payload = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
            self.wfile.flush()

        def on_record(rec: UrlRecord) -> None:
            # A write failure here means the browser navigated away or pressed
            # stop. core.run lets BrokenPipeError through, which unwinds the
            # whole run -- the same mechanism that makes `| head` work.
            chunk(
                {
                    "type": "url",
                    "domain": rec.domain,
                    "url": rec.url,
                    "source": rec.source,
                    "fetched_at": rec.fetched_at,
                }
            )

        try:
            chunk(
                {
                    "type": "start",
                    "domains": cfg.domains,
                    "sources": list(cfg.sources),
                    "analysis": cfg.analysis,
                    "timeline_granularity": cfg.timeline_granularity,
                    "provenance": cfg.provenance,
                    "source_summary": cfg.source_summary,
                }
            )
            if cfg.source_summary:
                source_result = asyncio.run(run_source_summary(cfg))
                write_source_summary_files(source_result, cfg)
                for domain in cfg.domains:
                    summary = source_result.summaries.get(domain)
                    if summary is None:
                        continue
                    chunk(
                        {
                            "type": "source_summary",
                            "record": summary.as_record(),
                            "text": format_source_summary(summary, cfg.out_format),
                        }
                    )
                stats = source_result.stats
                errors = source_result.errors
            elif cfg.analysis:
                history_result = asyncio.run(run_history(cfg))
                write_analysis_files(history_result, cfg, cfg.analysis)
                for domain in cfg.domains:
                    history = history_result.analyses.get(domain)
                    if history is None:
                        continue
                    for record in records_for(
                        history,
                        cfg.analysis,
                        timeline_granularity=cfg.timeline_granularity,
                    ):
                        chunk(
                            {
                                "type": "analysis",
                                "mode": cfg.analysis,
                                "record": record,
                                "text": format_record(record, cfg.out_format),
                            }
                        )
                stats = history_result.stats
                errors = history_result.errors
            else:
                url_result = asyncio.run(run(cfg, on_record=on_record))
                stats = url_result.stats
                errors = url_result.errors
            for st in stats:
                chunk(
                    {
                        "type": "stats",
                        "domain": st.domain,
                        "fetched": st.fetched,
                        "kept": st.kept,
                        "complete": st.complete,
                    }
                )
            for domain, exc in errors:
                chunk({"type": "error", "domain": domain, "message": str(exc)})
            chunk({"type": "done"})
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log.info("client disconnected; run cancelled")
            # Do not go back for another request on a socket the client has
            # already dropped: reading it would raise again, out of our hands.
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - surface any failure to the page
            log.exception("run failed")
            try:
                chunk({"type": "error", "domain": "", "message": str(exc)})
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass


def is_client_gone(exc: BaseException | None) -> bool:
    """Was this the viewer closing the tab rather than a fault worth reporting?"""
    return isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError))


def format_http_authority(host: str, port: int) -> str:
    """Format host:port for an HTTP URL/Host header, including IPv6 brackets."""
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{display_host}:{port}"


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        """Keep normal disconnects out of the terminal.

        A browser that navigates away, or a `curl | head` that stops reading,
        resets the connection. socketserver's default is to dump a traceback
        for it, which makes routine traffic look like a crash.
        """
        exc = sys.exc_info()[1]
        if is_client_gone(exc):
            log.debug("client %s went away: %s", client_address, exc)
            return
        super().handle_error(request, client_address)


class IPv6Server(Server):
    address_family = socket.AF_INET6


def serve(host: str = "127.0.0.1", port: int = 8765) -> tuple[Server, str]:
    """Start the server and return it together with its one-time token."""
    token = secrets.token_urlsafe(24)

    class Bound(Handler):
        pass

    Bound.token = token

    server_cls = IPv6Server if ":" in host else Server
    httpd = server_cls((host, port), Bound)
    bound_port = httpd.server_address[1]
    Bound.allowed_hosts = frozenset(
        {
            format_http_authority(host, bound_port).lower(),
            format_http_authority("localhost", bound_port),
            format_http_authority("127.0.0.1", bound_port),
            format_http_authority("::1", bound_port),
        }
    )

    threading.Thread(target=httpd.serve_forever, name="wayparam-gui", daemon=True).start()
    return httpd, token

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
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import __version__
from ..config import RunConfig, build_filter_options
from ..core import run
from ..http import HttpConfig
from ..normalize import NormalizeOptions
from ..output import UrlRecord
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
    domains = [
        d.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
        for d in raw_domains.replace(",", "\n").splitlines()
        if d.strip() and not d.strip().startswith("#")
    ]
    domains = list(dict.fromkeys(d for d in domains if d))
    if not domains:
        raise Rejected(400, "No domain given.")

    outdir = str(data.get("outdir", "") or "").strip()
    write_files = bool(outdir)

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(data.get(key, default))))
        except (TypeError, ValueError):
            return default

    return RunConfig(
        domains=domains,
        outdir=Path(outdir) if write_files else Path("results"),
        write_files=write_files,
        out_format="jsonl" if data.get("format") == "jsonl" else "txt",
        concurrency=_int("concurrency", 6, 1, 64),
        rps=max(0.0, float(data.get("rps") or 0.0)),
        http=HttpConfig(
            timeout_s=float(data.get("timeout") or 30.0),
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
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Rejected(400, "Body is not valid JSON.") from None

    # ---- responses ----------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
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
            self._check_token((parse_qs(path.query).get("t") or [None])[0])
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
            chunk({"type": "url", "domain": rec.domain, "url": rec.url})

        try:
            chunk({"type": "start", "domains": cfg.domains})
            result = asyncio.run(run(cfg, on_record=on_record))
            for st in result.stats:
                chunk({"type": "stats", "domain": st.domain, "fetched": st.fetched, "kept": st.kept})
            for domain, exc in result.errors:
                chunk({"type": "error", "domain": domain, "message": str(exc)})
            chunk({"type": "done"})
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log.info("client disconnected; run cancelled")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the page
            log.exception("run failed")
            try:
                chunk({"type": "error", "domain": "", "message": str(exc)})
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass


def serve(host: str = "127.0.0.1", port: int = 8765) -> tuple[http.server.ThreadingHTTPServer, str]:
    """Start the server and return it together with its one-time token."""
    token = secrets.token_urlsafe(24)

    class Bound(Handler):
        pass

    Bound.token = token

    httpd = http.server.ThreadingHTTPServer((host, port), Bound)
    httpd.daemon_threads = True
    bound_port = httpd.server_address[1]
    Bound.allowed_hosts = frozenset(
        {f"{host}:{bound_port}", f"localhost:{bound_port}", f"127.0.0.1:{bound_port}"}
    )

    threading.Thread(target=httpd.serve_forever, name="wayparam-gui", daemon=True).start()
    return httpd, token

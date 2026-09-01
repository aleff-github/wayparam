# SPDX-License-Identifier: GPL-3.0

"""Entry point for the wayparam web UI (`wayparam-gui`)."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

from .. import __version__
from .server import serve

log = logging.getLogger("wayparam.gui")

__all__ = ["main"]


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wayparam-gui",
        description="Serve the wayparam web UI on the loopback interface.",
    )
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    p.add_argument("--port", type=int, default=8765, help="Bind port, 0 to pick a free one.")
    p.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    p.add_argument("-v", "--verbose", action="count", default=0, help="Increase log verbosity.")
    p.add_argument("--version", action="version", version=f"wayparam-gui {__version__}")
    return p


def _open_browser(url: str) -> None:
    """Best effort: a confined snap cannot reach the host browser, and a
    headless or SSH session has none. The URL is on stderr either way."""
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        log.debug("could not open a browser", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG
        if args.verbose >= 2
        else logging.INFO
        if args.verbose
        else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        # The UI performs outbound requests for whoever can reach it, so it is
        # deliberately awkward to expose beyond the machine it runs on.
        print(
            f"Refusing to bind {args.host}: the wayparam UI is loopback-only. "
            "Use SSH port forwarding to reach it from another machine.",
            file=sys.stderr,
        )
        return 2

    try:
        httpd, token = serve(args.host, args.port)
    except OSError as exc:
        print(f"Cannot listen on {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{httpd.server_address[1]}/?t={token}"
    print(f"wayparam UI: {url}", file=sys.stderr)
    print("The token in that URL is required; press Ctrl+C to stop.", file=sys.stderr)

    if not args.no_browser:
        threading.Timer(0.3, _open_browser, args=(url,)).start()

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130
    finally:
        httpd.shutdown()
    return 0

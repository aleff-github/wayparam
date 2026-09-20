# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit


def normalize_domain(value: str) -> str | None:
    """Return a normalized host[:port] from a host or URL-like input.

    Paths, queries, fragments and userinfo are discarded. IPv6 literals keep
    the brackets required to distinguish the address from an optional port.
    Invalid ports and inputs without a host are rejected.
    """
    raw = value.strip()
    if not raw or raw.startswith("#"):
        return None

    candidate = raw if "://" in raw else f"//{raw}"
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None

    if not host:
        return None

    host = host.lower()
    if ":" in host:
        host = f"[{host}]"

    return f"{host}:{port}" if port is not None else host


def parse_domains(values: Iterable[str]) -> list[str]:
    """Normalize, discard comments/invalid entries and deduplicate in order."""
    out: list[str] = []
    seen: set[str] = set()

    for value in values:
        domain = normalize_domain(value)
        if domain is None or domain in seen:
            continue
        seen.add(domain)
        out.append(domain)

    return out


def read_domains(path: str) -> list[str]:
    """Read domains from a file, or from stdin when path is '-'."""
    if path == "-":
        content = sys.stdin.read().splitlines()
    else:
        content = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()

    return parse_domains(content)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

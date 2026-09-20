# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

SourceName = Literal["wayback", "commoncrawl"]
SOURCE_NAMES: tuple[SourceName, ...] = ("wayback", "commoncrawl")


@dataclass(frozen=True)
class SourceRecord:
    """One archived URL discovered by a configured source."""

    original: str
    source: SourceName
    timestamp: str | None = None
    status_code: str | None = None
    mime_type: str | None = None


class UrlProvider(Protocol):
    name: SourceName

    def iter_urls(
        self, domain: str, *, client: httpx.AsyncClient
    ) -> AsyncGenerator[SourceRecord, None]:
        """Yield archived URL records for one target domain."""
        ...


def parse_source_names(value: str) -> tuple[SourceName, ...]:
    """Parse a comma-separated provider list while preserving user order."""
    out: list[SourceName] = []
    for raw in value.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name not in SOURCE_NAMES:
            allowed = ", ".join(SOURCE_NAMES)
            raise ValueError(f"unknown source {name!r}; choose from {allowed}")
        typed: SourceName = name
        if typed not in out:
            out.append(typed)
    if not out:
        raise ValueError("at least one source is required")
    return tuple(out)

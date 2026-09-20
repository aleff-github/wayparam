# SPDX-License-Identifier: GPL-3.0

"""Common Crawl CDXJ index provider.

The public index API is intentionally used as a lookup service, not as a bulk
export mechanism. Requests are serialized and rate-limited per run so multiple
wayparam domains do not hammer index.commoncrawl.org concurrently.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace

import httpx

from .. import __version__
from ..http import HttpConfig, get_text, iter_lines
from ..ratelimit import RateLimiter
from ..wayback import CdxOptions
from .base import SourceName, SourceRecord

COLLINFO_ENDPOINT = "https://index.commoncrawl.org/collinfo.json"
INDEX_ROOT = "https://index.commoncrawl.org"
_INDEX_RE = re.compile(r"^CC-MAIN-\d{4}-\d{2}$")
_DEFAULT_UA = (
    f"wayparam/{__version__} (Common Crawl index client; +https://github.com/aleff-github/wayparam)"
)


@dataclass(frozen=True)
class CommonCrawlOptions:
    indexes: tuple[str, ...] = ("latest",)
    page_size: int = 5
    rps: float = 1.0
    filters: tuple[str, ...] = ()


def parse_page_count(text: str) -> int | None:
    """Parse the Common Crawl showNumPages JSON response."""
    stripped = text.strip()
    if not stripped:
        return 0
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    pages = payload.get("pages")
    if isinstance(pages, bool) or not isinstance(pages, (int, str)):
        return None
    try:
        parsed = int(pages)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def parse_index_record(line: str) -> SourceRecord | None:
    """Turn one newline-delimited Common Crawl JSON object into a record."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    url = payload.get("url")
    if not isinstance(url, str) or not url:
        return None

    def optional_text(key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        return str(value)

    return SourceRecord(
        original=url,
        source="commoncrawl",
        timestamp=optional_text("timestamp"),
        status_code=optional_text("status"),
        mime_type=optional_text("mime"),
    )


class CommonCrawlProvider:
    name: SourceName = "commoncrawl"

    def __init__(
        self,
        *,
        options: CommonCrawlOptions,
        query: CdxOptions,
        http: HttpConfig,
    ):
        self._options = options
        self._query = query
        self._http = http if http.user_agent else replace(http, user_agent=_DEFAULT_UA)
        self._rate_limiter = RateLimiter(options.rps) if options.rps > 0 else None
        self._request_lock: asyncio.Lock | None = None
        self._resolve_lock: asyncio.Lock | None = None
        self._resolved: list[tuple[str, str]] | None = None

    def _request_guard(self) -> asyncio.Lock:
        if self._request_lock is None:
            self._request_lock = asyncio.Lock()
        return self._request_lock

    def _resolve_guard(self) -> asyncio.Lock:
        if self._resolve_lock is None:
            self._resolve_lock = asyncio.Lock()
        return self._resolve_lock

    async def _before_request(self) -> None:
        if self._rate_limiter:
            await self._rate_limiter.wait()

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: list[tuple[str, str]] | None = None,
    ) -> str:
        async with self._request_guard():
            await self._before_request()
            return await get_text(
                client,
                url,
                params=params,
                config=self._http,
                empty_statuses={404},
            )

    async def _stream(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: list[tuple[str, str]],
    ) -> AsyncGenerator[str, None]:
        async with self._request_guard():
            await self._before_request()
            async for line in iter_lines(
                client,
                url,
                params=params,
                config=self._http,
                empty_statuses={404},
            ):
                yield line

    async def _resolve_indexes(self, client: httpx.AsyncClient) -> list[tuple[str, str]]:
        if self._resolved is not None:
            return self._resolved

        async with self._resolve_guard():
            if self._resolved is not None:
                return self._resolved

            requested = self._options.indexes or ("latest",)
            latest: tuple[str, str] | None = None
            if any(value.lower() == "latest" for value in requested):
                raw = await self._get(client, COLLINFO_ENDPOINT)
                try:
                    info = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Common Crawl returned invalid collection metadata") from exc
                if not isinstance(info, list) or not info:
                    raise RuntimeError("Common Crawl collection metadata is empty")
                first = info[0]
                if not isinstance(first, dict):
                    raise RuntimeError("Common Crawl collection metadata is malformed")
                index_id = first.get("id")
                endpoint = first.get("cdx-api")
                if not isinstance(index_id, str) or not isinstance(endpoint, str):
                    raise RuntimeError("Common Crawl latest collection has no usable CDX endpoint")
                latest = (index_id, endpoint)

            resolved: list[tuple[str, str]] = []
            seen: set[str] = set()
            for requested_id in requested:
                if requested_id.lower() == "latest":
                    assert latest is not None
                    index_id, endpoint = latest
                else:
                    index_id = requested_id.upper()
                    if not _INDEX_RE.fullmatch(index_id):
                        raise ValueError(
                            f"invalid Common Crawl index {requested_id!r}; "
                            "expected CC-MAIN-YYYY-NN or latest"
                        )
                    endpoint = f"{INDEX_ROOT}/{index_id}-index"

                if index_id in seen:
                    continue
                seen.add(index_id)
                resolved.append((index_id, endpoint))

            self._resolved = resolved
            return resolved

    def _base_params(self, domain: str) -> list[tuple[str, str]]:
        match_type = "domain" if self._query.include_subdomains else "host"
        params: list[tuple[str, str]] = [("url", domain), ("matchType", match_type)]
        if self._query.collapse:
            params.append(("collapse", self._query.collapse))
        if self._query.from_ts:
            params.append(("from", self._query.from_ts))
        if self._query.to_ts:
            params.append(("to", self._query.to_ts))
        for value in self._options.filters:
            params.append(("filter", value))
        return params

    async def _page_count(
        self, client: httpx.AsyncClient, endpoint: str, domain: str
    ) -> int | None:
        params = self._base_params(domain)
        params += [
            ("showNumPages", "true"),
            ("pageSize", str(self._options.page_size)),
        ]
        return parse_page_count(await self._get(client, endpoint, params=params))

    async def _iter_index(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        domain: str,
    ) -> AsyncGenerator[SourceRecord, None]:
        pages = await self._page_count(client, endpoint, domain)
        if pages is None:
            # The API normally returns {"pages", "pageSize", "blocks"}. A
            # defensive single page is preferable to silently dropping a
            # source if that metadata endpoint changes temporarily.
            pages = 1

        for page in range(pages):
            params = self._base_params(domain)
            params += [
                ("output", "json"),
                ("pageSize", str(self._options.page_size)),
                ("page", str(page)),
            ]
            async for line in self._stream(client, endpoint, params=params):
                record = parse_index_record(line)
                if record is None:
                    raise ValueError(f"Malformed Common Crawl index row: {line[:160]}")
                yield record

    async def iter_urls(
        self, domain: str, *, client: httpx.AsyncClient
    ) -> AsyncGenerator[SourceRecord, None]:
        for _index_id, endpoint in await self._resolve_indexes(client):
            async for record in self._iter_index(client, endpoint, domain):
                yield record

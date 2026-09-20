# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx

from ..http import HttpConfig
from ..ratelimit import RateLimiter
from ..wayback import CdxOptions, iter_original_urls
from .base import SourceName, SourceRecord


class WaybackProvider:
    name: SourceName = "wayback"

    def __init__(self, *, cdx: CdxOptions, http: HttpConfig, rps: float):
        self._cdx = cdx
        self._http = http
        self._rate_limiter = RateLimiter(rps) if rps > 0 else None

    async def iter_urls(
        self, domain: str, *, client: httpx.AsyncClient
    ) -> AsyncGenerator[SourceRecord, None]:
        urls = iter_original_urls(
            domain,
            client=client,
            http_config=self._http,
            rate_limiter=self._rate_limiter,
            opt=self._cdx,
        )
        try:
            async for url in urls:
                yield SourceRecord(original=url, source="wayback")
        finally:
            await urls.aclose()

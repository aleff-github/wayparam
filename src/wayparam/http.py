# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

DEFAULT_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/121.0",
]


@dataclass(frozen=True)
class HttpConfig:
    timeout_s: float = 30.0
    retries: int = 4
    backoff_base_s: float = 0.7
    max_backoff_s: float = 12.0
    user_agent: str | None = None
    proxy: str | None = None


# Picked once per process: rotating the User-Agent between pages of the same
# pagination sequence serves no purpose and looks like evasion to the archive.
_SESSION_UA = random.choice(DEFAULT_UAS)


def _pick_ua(config: HttpConfig) -> str:
    return config.user_agent or _SESSION_UA


def _backoff(config: HttpConfig, attempt: int) -> float:
    """Exponential backoff with full jitter.

    Without the jitter, concurrent domains that all hit a 429 retry in
    lockstep and keep hammering the API at the same instants.
    """
    ceiling = min(config.backoff_base_s * (2**attempt), config.max_backoff_s)
    return random.uniform(0.0, ceiling)


async def iter_lines(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: list[tuple[str, str]] | None = None,
    config: HttpConfig,
) -> AsyncIterator[str]:
    """Yield the response's non-empty lines as they arrive.

    A single CDX block page runs to tens of megabytes, and buffering one costs
    twice its size -- the raw bytes and the decoded text -- before a single URL
    reaches the caller. Streaming keeps that flat and lets results appear while
    the page is still downloading.

    A retry restarts the request from the beginning, since there is no way to
    resume a body mid-flight. Lines already delivered are counted and skipped
    on the way back, so the caller never sees one twice.
    """
    headers = {"User-Agent": _pick_ua(config)}
    query = httpx.QueryParams(tuple(params)) if params is not None else None
    delivered = 0
    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(config.retries + 1):
        try:
            async with client.stream(
                "GET", url, params=query, headers=headers, timeout=config.timeout_s
            ) as resp:
                last_status = resp.status_code

                if resp.status_code in (429, 503):
                    if attempt >= config.retries:
                        break
                    await _sleep_before_retry(resp.headers.get("Retry-After"), config, attempt)
                    continue

                resp.raise_for_status()

                seen_here = 0
                async for raw in resp.aiter_lines():
                    line = raw.strip()
                    if not line:
                        continue
                    seen_here += 1
                    # Everything up to `delivered` went out before the retry.
                    if seen_here <= delivered:
                        continue
                    delivered += 1
                    yield line
                return

        except (httpx.TransportError, httpx.DecodingError, httpx.HTTPStatusError) as e:
            last_exc = e
            if attempt >= config.retries:
                break
            await asyncio.sleep(_backoff(config, attempt))

    detail = f"status={last_status}" if last_status else "no-status"
    raise RuntimeError(f"HTTP request failed after retries ({detail}): {url}") from last_exc


async def _sleep_before_retry(retry_after: str | None, config: HttpConfig, attempt: int) -> None:
    if retry_after and retry_after.isdigit():
        await asyncio.sleep(min(int(retry_after), config.max_backoff_s))
    else:
        await asyncio.sleep(_backoff(config, attempt))


async def get_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: list[tuple[str, str]] | None = None,
    config: HttpConfig,
) -> str:
    headers = {"User-Agent": _pick_ua(config)}
    # httpx accepts a list of pairs, but `list` is invariant, so a
    # list[tuple[str, str]] does not type-check against it. A tuple is
    # covariant and does. QueryParams keeps repeated keys (`filter=` is sent
    # more than once), which a dict would silently collapse.
    query = httpx.QueryParams(tuple(params)) if params is not None else None
    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(config.retries + 1):
        try:
            resp = await client.get(url, params=query, headers=headers, timeout=config.timeout_s)

            last_status = resp.status_code
            if resp.status_code in (429, 503):
                if attempt >= config.retries:
                    break  # last attempt: sleeping before giving up buys nothing
                await _sleep_before_retry(resp.headers.get("Retry-After"), config, attempt)
                continue

            resp.raise_for_status()
            return resp.text

        # TransportError rather than the narrower NetworkError: the CDX API
        # does truncate large chunked responses, and httpx reports that as
        # RemoteProtocolError -- a transient failure worth retrying that is not
        # a NetworkError.
        except (httpx.TransportError, httpx.DecodingError, httpx.HTTPStatusError) as e:
            last_exc = e
            if attempt >= config.retries:
                break
            await asyncio.sleep(_backoff(config, attempt))

    detail = f"status={last_status}" if last_status else "no-status"
    raise RuntimeError(f"HTTP request failed after retries ({detail}): {url}") from last_exc

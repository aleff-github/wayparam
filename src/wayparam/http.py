# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import asyncio
import random
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


async def get_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: list[tuple[str, str]] | None = None,
    config: HttpConfig,
) -> str:
    headers = {"User-Agent": _pick_ua(config)}
    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(config.retries + 1):
        try:
            resp = await client.get(url, params=params, headers=headers, timeout=config.timeout_s)

            last_status = resp.status_code
            if resp.status_code in (429, 503):
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    await asyncio.sleep(min(int(retry_after), config.max_backoff_s))
                else:
                    await asyncio.sleep(_backoff(config, attempt))
                continue

            resp.raise_for_status()
            return resp.text

        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
            last_exc = e
            if attempt >= config.retries:
                break
            await asyncio.sleep(_backoff(config, attempt))

    detail = f"status={last_status}" if last_status else "no-status"
    raise RuntimeError(f"HTTP request failed after retries ({detail}): {url}") from last_exc

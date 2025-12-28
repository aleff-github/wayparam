# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from .http import HttpConfig, get_text
from .ratelimit import RateLimiter

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"


@dataclass(frozen=True)
class CdxOptions:
    include_subdomains: bool = False
    collapse: str | None = "urlkey"
    from_ts: str | None = None
    to_ts: str | None = None
    limit: int = 50000
    filters: list[str] | None = None
    match_type: str | None = None


_resume_key_re = re.compile(r"^resumeKey:?\s*(.+)$", re.IGNORECASE)


def _build_params(domain: str, opt: CdxOptions, resume_key: str | None) -> list[tuple[str, str]]:
    match_type = opt.match_type or ("domain" if opt.include_subdomains else "host")

    params: list[tuple[str, str]] = [
        ("url", domain),
        ("matchType", match_type),
        ("output", "txt"),
        ("fl", "original"),
        ("showResumeKey", "true"),
        ("limit", str(opt.limit)),
    ]
    if opt.collapse:
        params.append(("collapse", opt.collapse))
    if opt.from_ts:
        params.append(("from", opt.from_ts))
    if opt.to_ts:
        params.append(("to", opt.to_ts))
    if opt.filters:
        for f in opt.filters:
            params.append(("filter", f))
    if resume_key:
        params.append(("resumeKey", resume_key))
    return params


def _split_urls_and_resume_key(text: str) -> tuple[list[str], str | None]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], None

    last = lines[-1]
    m = _resume_key_re.match(last)
    if m:
        return lines[:-1], (m.group(1).strip() or None)

    if "://" not in last and not last.lower().startswith(("http:", "https:")):
        return lines[:-1], last

    return lines, None


async def iter_original_urls(
    domain: str,
    *,
    client: httpx.AsyncClient,
    http_config: HttpConfig,
    rate_limiter: RateLimiter | None,
    opt: CdxOptions,
) -> AsyncIterator[str]:
    resume_key: str | None = None
    seen_resume_keys: set[str] = set()

    while True:
        if rate_limiter:
            await rate_limiter.wait()

        params = _build_params(domain, opt, resume_key)
        text = await get_text(client, CDX_ENDPOINT, params=params, config=http_config)

        urls, new_resume_key = _split_urls_and_resume_key(text)
        for u in urls:
            yield u

        if not new_resume_key:
            break

        if new_resume_key in seen_resume_keys:
            break
        seen_resume_keys.add(new_resume_key)
        resume_key = new_resume_key

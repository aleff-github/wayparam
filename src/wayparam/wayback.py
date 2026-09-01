# SPDX-License-Identifier: GPL-3.0

"""Talking to the Wayback CDX API, including the two ways it paginates.

The endpoint offers two pagination mechanisms, and they are not equivalent:

* ``showResumeKey`` walks the filtered result with an opaque cursor. It costs
  one request per ``limit`` rows, but -- measured against the live API -- the
  server drops exactly one row at each page boundary while ``collapse`` is on.
* ``showNumPages``/``page`` walks the *index* in fixed blocks. It is lossless
  with ``collapse``, but the number of requests follows the size of the
  domain's index rather than the size of the filtered result.

The default (``auto``) sends one resumeKey request: a result that fits in a
single response has no boundary, so nothing can be lost and the block API would
only cost extra requests. Only a genuinely multi-page result is worth paying
for the block walk.

Block pages are streamed line by line, because their size is bounded by the
domain's index rather than by ``--limit``; the resumeKey walk buffers, because
finding the cursor means looking at the last line and its pages are capped at
``--limit`` rows anyway.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

import httpx

from .http import HttpConfig, get_text, iter_lines
from .ratelimit import RateLimiter

log = logging.getLogger("wayparam")

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"

PAGINATION_MODES = ("auto", "blocks", "resume")

# Asking for the row format turns showNumPages into a bare "-", so the page
# count has to be requested without it.
_OUTPUT_PARAMS = [("output", "txt"), ("fl", "original")]


@dataclass(frozen=True)
class CdxOptions:
    include_subdomains: bool = False
    collapse: str | None = "urlkey"
    from_ts: str | None = None
    to_ts: str | None = None
    limit: int = 50000
    filters: list[str] | None = None
    match_type: str | None = None
    #: One of PAGINATION_MODES.
    pagination: str = "auto"
    #: Index blocks per request in block mode. Bigger means fewer, slower,
    #: heavier responses; the first results appear only once one arrives.
    block_size: int = 100


_resume_key_re = re.compile(r"^resumeKey:?\s*(.+)$", re.IGNORECASE)


def _filter_params(domain: str, opt: CdxOptions) -> list[tuple[str, str]]:
    """The parameters that select rows, shared by every request shape."""
    match_type = opt.match_type or ("domain" if opt.include_subdomains else "host")

    params: list[tuple[str, str]] = [("url", domain), ("matchType", match_type)]
    if opt.collapse:
        params.append(("collapse", opt.collapse))
    if opt.from_ts:
        params.append(("from", opt.from_ts))
    if opt.to_ts:
        params.append(("to", opt.to_ts))
    for f in opt.filters or []:
        params.append(("filter", f))
    return params


def _build_params(domain: str, opt: CdxOptions, resume_key: str | None) -> list[tuple[str, str]]:
    """One page of the resumeKey walk."""
    params = _filter_params(domain, opt) + _OUTPUT_PARAMS
    params += [("showResumeKey", "true"), ("limit", str(opt.limit))]
    if resume_key:
        params.append(("resumeKey", resume_key))
    return params


def _build_block_params(domain: str, opt: CdxOptions, page: int) -> list[tuple[str, str]]:
    """One block of the page/pageSize walk."""
    params = _filter_params(domain, opt) + _OUTPUT_PARAMS
    params += [("pageSize", str(opt.block_size)), ("page", str(page))]
    return params


def _build_num_pages_params(domain: str, opt: CdxOptions) -> list[tuple[str, str]]:
    """How many blocks the query spans.

    Deliberately without _OUTPUT_PARAMS: asking for output=txt&fl=original here
    makes the endpoint answer "-" no matter what, which reads as "pagination
    unavailable" and silently sends every run down the lossy path.
    """
    params = _filter_params(domain, opt)
    params += [("showNumPages", "true"), ("pageSize", str(opt.block_size))]
    return params


def parse_num_pages(text: str) -> int | None:
    """The page count, or None when the API says pagination is unavailable."""
    stripped = text.strip()
    try:
        pages = int(stripped)
    except ValueError:
        return None
    return max(0, pages)


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


async def _get(
    url_params: list[tuple[str, str]],
    *,
    client: httpx.AsyncClient,
    http_config: HttpConfig,
    rate_limiter: RateLimiter | None,
) -> str:
    if rate_limiter:
        await rate_limiter.wait()
    return await get_text(client, CDX_ENDPOINT, params=url_params, config=http_config)


async def _iter_blocks(
    domain: str,
    pages: int,
    *,
    client: httpx.AsyncClient,
    http_config: HttpConfig,
    rate_limiter: RateLimiter | None,
    opt: CdxOptions,
) -> AsyncIterator[str]:
    for page in range(pages):
        # Streamed, not buffered: a block page is the one response that can run
        # to tens of megabytes, and the whole point of asking for many blocks at
        # once is to make it big.
        if rate_limiter:
            await rate_limiter.wait()
        async for line in iter_lines(
            client,
            CDX_ENDPOINT,
            params=_build_block_params(domain, opt, page),
            config=http_config,
        ):
            yield line


async def _iter_resume(
    domain: str,
    *,
    client: httpx.AsyncClient,
    http_config: HttpConfig,
    rate_limiter: RateLimiter | None,
    opt: CdxOptions,
) -> AsyncIterator[str]:
    resume_key: str | None = None
    seen_resume_keys: set[str] = set()
    warned_about_collapse = False

    while True:
        text = await _get(
            _build_params(domain, opt, resume_key),
            client=client,
            http_config=http_config,
            rate_limiter=rate_limiter,
        )

        urls, new_resume_key = _split_urls_and_resume_key(text)
        for u in urls:
            yield u

        if not new_resume_key:
            break

        if opt.collapse and not warned_about_collapse:
            # Measured against the live API: once a request resumes from a
            # resumeKey, collapsing drops exactly one row at each page
            # boundary. --pagination blocks does not have this problem.
            log.warning(
                "%s: resumeKey paging with collapse=%s drops one URL per page boundary. "
                "Use --pagination blocks (the default) or --no-collapse for a complete "
                "result set.",
                domain,
                opt.collapse,
            )
            warned_about_collapse = True

        if new_resume_key in seen_resume_keys:
            break
        seen_resume_keys.add(new_resume_key)
        resume_key = new_resume_key


async def iter_original_urls(
    domain: str,
    *,
    client: httpx.AsyncClient,
    http_config: HttpConfig,
    rate_limiter: RateLimiter | None,
    opt: CdxOptions,
    # AsyncGenerator, not AsyncIterator: callers close this explicitly when they
    # stop early, and only a generator offers aclose().
) -> AsyncGenerator[str, None]:
    mode = opt.pagination if opt.pagination in PAGINATION_MODES else "auto"

    if mode == "auto":
        text = await _get(
            _build_params(domain, opt, None),
            client=client,
            http_config=http_config,
            rate_limiter=rate_limiter,
        )
        urls, resume_key = _split_urls_and_resume_key(text)
        if not resume_key:
            # Single response, so there is no page boundary to lose anything
            # at. This is the cheap and exact path for most queries.
            for u in urls:
                yield u
            return
        # More than one page: the resumeKey walk would start losing rows here.
        # Discard the probe and take the block walk instead, so everything
        # yielded (and counted) comes from one consistent pass.
        mode = "blocks"

    if mode == "blocks":
        pages = parse_num_pages(
            await _get(
                _build_num_pages_params(domain, opt),
                client=client,
                http_config=http_config,
                rate_limiter=rate_limiter,
            )
        )
        if pages is not None:
            async for u in _iter_blocks(
                domain,
                pages,
                client=client,
                http_config=http_config,
                rate_limiter=rate_limiter,
                opt=opt,
            ):
                yield u
            return
        log.warning(
            "%s: the CDX block pagination API is unavailable for this query; "
            "falling back to resumeKey paging.",
            domain,
        )

    async for u in _iter_resume(
        domain,
        client=client,
        http_config=http_config,
        rate_limiter=rate_limiter,
        opt=opt,
    ):
        yield u

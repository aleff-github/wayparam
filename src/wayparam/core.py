# SPDX-License-Identifier: GPL-3.0

"""Orchestration: fetch, filter, normalize and emit URLs for a set of domains.

This module knows nothing about argparse, terminals or HTTP servers. Frontends
build a RunConfig, optionally pass a callback to observe records as they are
produced, and get a RunResult back.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import RunConfig
from .filters import is_boring
from .normalize import canonicalize_url
from .output import UrlRecord, now_utc_iso, open_outfile, write_record
from .ratelimit import RateLimiter
from .wayback import iter_original_urls

log = logging.getLogger("wayparam")

RecordCallback = Callable[[UrlRecord], None]


@dataclass(frozen=True)
class DomainStats:
    domain: str
    fetched: int
    kept: int


@dataclass
class RunResult:
    stats: list[DomainStats] = field(default_factory=list)
    errors: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def client_kwargs(proxy: str | None, limits: httpx.Limits) -> dict:
    """Support httpx 'proxy' (new) and 'proxies' (old) without pinning versions."""
    kwargs: dict = {"limits": limits, "follow_redirects": True}
    if not proxy:
        return kwargs

    try:
        params = inspect.signature(httpx.AsyncClient).parameters
        kwargs["proxy" if "proxy" in params else "proxies"] = proxy
    except Exception:
        # Fallback: try the new name first
        kwargs["proxy"] = proxy
    return kwargs


def _outfile_for(outdir: Path, domain: str, out_format: str) -> Path:
    return outdir / f"{domain}.{'jsonl' if out_format == 'jsonl' else 'txt'}"


async def _process_domain(
    domain: str,
    cfg: RunConfig,
    *,
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter | None,
    on_record: RecordCallback | None,
) -> DomainStats:
    fetched = 0
    kept = 0
    seen: set[str] = set()

    out_fh = open_outfile(_outfile_for(cfg.outdir, domain, cfg.out_format)) if cfg.write_files else None

    try:
        async for raw in iter_original_urls(
            domain,
            client=client,
            http_config=cfg.http,
            rate_limiter=rate_limiter,
            opt=cfg.cdx,
        ):
            fetched += 1

            # Filter before canonicalizing: most archived URLs are static
            # assets, so the early exit is what keeps the common path cheap.
            # Re-checking the canonical form afterwards would be redundant --
            # canonicalization leaves the path untouched, which is all
            # is_boring() looks at.
            if is_boring(raw, cfg.filters):
                continue

            canon = canonicalize_url(raw, cfg.normalize)
            if canon is None:
                continue

            if canon in seen:
                continue
            seen.add(canon)

            kept += 1
            rec = UrlRecord(domain=domain, url=canon, fetched_at=now_utc_iso())

            if out_fh:
                write_record(out_fh, rec, cfg.out_format)
            if on_record:
                on_record(rec)

    finally:
        if out_fh:
            out_fh.close()

    return DomainStats(domain=domain, fetched=fetched, kept=kept)


async def run(cfg: RunConfig, *, on_record: RecordCallback | None = None) -> RunResult:
    """Process every domain in `cfg`, collecting stats and per-domain errors.

    BrokenPipeError is never collected: a closed output is the consumer going
    away, which concerns the whole run rather than one domain.
    """
    if cfg.write_files:
        cfg.outdir.mkdir(parents=True, exist_ok=True)

    limits = httpx.Limits(
        max_connections=max(10, cfg.concurrency * 4),
        max_keepalive_connections=max(10, cfg.concurrency * 2),
    )
    rate_limiter = RateLimiter(cfg.rps) if cfg.rps > 0 else None
    sem = asyncio.Semaphore(max(1, cfg.concurrency))

    async def guarded(domain: str) -> DomainStats:
        async with sem:
            return await _process_domain(
                domain,
                cfg,
                client=client,
                rate_limiter=rate_limiter,
                on_record=on_record,
            )

    async with httpx.AsyncClient(**client_kwargs(cfg.http.proxy, limits)) as client:
        tasks = [asyncio.create_task(guarded(d)) for d in cfg.domains]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    out = RunResult()
    for domain, r in zip(cfg.domains, results):
        if isinstance(r, BrokenPipeError):
            raise r
        if isinstance(r, BaseException):
            out.errors.append((domain, r))  # type: ignore[arg-type]
            continue
        out.stats.append(r)
        log.info("%s: fetched=%d kept=%d", r.domain, r.fetched, r.kept)
    return out

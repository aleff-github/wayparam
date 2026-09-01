# SPDX-License-Identifier: GPL-3.0

"""Orchestration: fetch, filter, normalize and emit URLs for a set of domains.

This module knows nothing about argparse, terminals or HTTP servers. Frontends
build a RunConfig, optionally pass a callback to observe records as they are
produced, and get a RunResult back.
"""

from __future__ import annotations

import asyncio
import hashlib
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

#: Called as (domain, fetched, kept) while a domain is still being processed.
ProgressCallback = Callable[[str, int, int], None]

# One CDX page is up to 50k rows, so progress has to come from inside a page.
_PROGRESS_EVERY = 1000
# A run can be killed at any point; an unflushed buffer would lose the tail.
_FLUSH_EVERY = 200


@dataclass(frozen=True)
class DomainStats:
    domain: str
    fetched: int
    kept: int
    #: False when the domain stopped before the CDX stream was exhausted --
    #: it failed, or the run hit its global --max-results budget.
    complete: bool = True


@dataclass
class RunResult:
    stats: list[DomainStats] = field(default_factory=list)
    errors: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Budget:
    """Global cap on emitted records, shared by every domain in a run.

    asyncio runs these coroutines on one thread, and there is no await between
    the check and the increment, so a plain counter is enough.
    """

    def __init__(self, limit: int):
        self.limit = max(0, limit)
        self.used = 0

    @property
    def exhausted(self) -> bool:
        return self.limit > 0 and self.used >= self.limit

    def take(self) -> bool:
        """Claim one slot. False means the run has produced all it was asked for."""
        if self.limit <= 0:
            return True
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


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


def fingerprint(url: str) -> int:
    """A compact stand-in for a URL in the per-domain dedup set.

    That set holds one entry for every URL a domain emits and lives for the
    whole domain, so it is what bounds a large run. Measured over 300k typical
    URLs, keeping the strings costs ~148 bytes per entry against ~72 for a
    16-byte digest -- about 340 MB instead of 710 MB at five million URLs.

    128 bits rather than 64: a collision here would silently drop a URL, which
    is the one failure mode this tool must not have. At a hundred million URLs
    the birthday probability is around 1e-23, against 3e-4 for 64 bits.

    blake2b and not the built-in hash(): hash() is randomised per process, and
    a fingerprint that changes between runs is a trap for anyone who later
    wants to persist or compare these.
    """
    return int.from_bytes(hashlib.blake2b(url.encode("utf-8"), digest_size=16).digest(), "big")


def _outfile_for(outdir: Path, domain: str, out_format: str) -> Path:
    return outdir / f"{domain}.{'jsonl' if out_format == 'jsonl' else 'txt'}"


async def _process_domain(
    domain: str,
    cfg: RunConfig,
    *,
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter | None,
    on_record: RecordCallback | None,
    on_progress: ProgressCallback | None,
    budget: Budget,
) -> tuple[DomainStats, Exception | None]:
    """Process one domain, returning what it produced *and* what stopped it.

    A domain that dies halfway through pagination has still produced real
    output, so the failure is returned alongside the stats instead of
    destroying them.
    """
    fetched = 0
    kept = 0
    complete = True
    error: Exception | None = None
    seen: set[int] = set()

    out_fh = (
        open_outfile(_outfile_for(cfg.outdir, domain, cfg.out_format)) if cfg.write_files else None
    )

    try:
        async for raw in iter_original_urls(
            domain,
            client=client,
            http_config=cfg.http,
            rate_limiter=rate_limiter,
            opt=cfg.cdx,
        ):
            fetched += 1
            if on_progress and fetched % _PROGRESS_EVERY == 0:
                on_progress(domain, fetched, kept)

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

            fp = fingerprint(canon)
            if fp in seen:
                continue
            seen.add(fp)

            if not budget.take():
                complete = False
                break

            kept += 1
            rec = UrlRecord(domain=domain, url=canon, fetched_at=now_utc_iso())

            if out_fh:
                write_record(out_fh, rec, cfg.out_format)
                if kept % _FLUSH_EVERY == 0:
                    out_fh.flush()
            if on_record:
                on_record(rec)

    except BrokenPipeError:
        # The consumer went away: that ends the whole run, not this domain.
        raise
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        complete = False
        error = exc
    finally:
        if out_fh:
            out_fh.close()

    stats = DomainStats(domain=domain, fetched=fetched, kept=kept, complete=complete)
    return stats, error


async def run(
    cfg: RunConfig,
    *,
    on_record: RecordCallback | None = None,
    on_progress: ProgressCallback | None = None,
) -> RunResult:
    """Process every domain in `cfg`, collecting stats and per-domain errors.

    A domain that fails still contributes the stats it managed to produce, so
    `RunResult.stats` and `RunResult.errors` can both mention the same domain.
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
    budget = Budget(cfg.max_results)

    async def guarded(domain: str) -> tuple[DomainStats, Exception | None]:
        async with sem:
            if budget.exhausted:
                # The cap was reached while this domain waited its turn; there
                # is nothing to fetch it for.
                return DomainStats(domain=domain, fetched=0, kept=0, complete=False), None
            return await _process_domain(
                domain,
                cfg,
                client=client,
                rate_limiter=rate_limiter,
                on_record=on_record,
                on_progress=on_progress,
                budget=budget,
            )

    async with httpx.AsyncClient(**client_kwargs(cfg.http.proxy, limits)) as client:
        tasks = [asyncio.create_task(guarded(d)) for d in cfg.domains]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    out = RunResult()
    for domain, r in zip(cfg.domains, results):
        if isinstance(r, BrokenPipeError):
            raise r
        if isinstance(r, BaseException):
            # Nothing came back at all: no stats to report for this domain.
            out.errors.append((domain, r))  # type: ignore[arg-type]
            continue
        stats, error = r
        out.stats.append(stats)
        if error is not None:
            out.errors.append((domain, error))
        log.info(
            "%s: fetched=%d kept=%d%s",
            stats.domain,
            stats.fetched,
            stats.kept,
            "" if stats.complete else " (incomplete)",
        )
    return out

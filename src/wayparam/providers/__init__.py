# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

from .base import SOURCE_NAMES, SourceName, SourceRecord, UrlProvider, parse_source_names
from .commoncrawl import CommonCrawlOptions, CommonCrawlProvider
from .wayback import WaybackProvider


def build_providers(cfg) -> list[UrlProvider]:
    """Instantiate configured sources in deterministic user-specified order."""
    providers: list[UrlProvider] = []
    for source in cfg.sources:
        if source == "wayback":
            providers.append(WaybackProvider(cdx=cfg.cdx, http=cfg.http, rps=cfg.rps))
        elif source == "commoncrawl":
            providers.append(
                CommonCrawlProvider(
                    options=cfg.commoncrawl,
                    query=cfg.cdx,
                    http=cfg.http,
                )
            )
        else:  # defensive for programmatic RunConfig construction
            raise ValueError(f"unsupported source: {source}")
    return providers


__all__ = [
    "SOURCE_NAMES",
    "CommonCrawlOptions",
    "SourceName",
    "SourceRecord",
    "UrlProvider",
    "build_providers",
    "parse_source_names",
]

# SPDX-License-Identifier: GPL-3.0

"""Frontend-independent description of one wayparam run.

The CLI, the web UI and any embedding code all build a RunConfig and hand it
to `core.run`, so none of them needs to know how the others gather settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .filters import DEFAULT_EXT_BLACKLIST, FilterOptions, parse_ext_set
from .http import HttpConfig
from .normalize import NormalizeOptions
from .output import OutputFormat
from .wayback import CdxOptions


@dataclass(frozen=True)
class RunConfig:
    domains: list[str]
    outdir: Path = Path("results")
    write_files: bool = True
    out_format: OutputFormat = "txt"
    #: Stop the whole run after this many emitted records (0 = no cap).
    max_results: int = 0
    concurrency: int = 6
    rps: float = 0.0
    http: HttpConfig = field(default_factory=HttpConfig)
    cdx: CdxOptions = field(default_factory=CdxOptions)
    normalize: NormalizeOptions = field(default_factory=NormalizeOptions)
    filters: FilterOptions = field(
        default_factory=lambda: FilterOptions(ext_blacklist=set(DEFAULT_EXT_BLACKLIST))
    )


def build_filter_options(
    ext_blacklist: str | None = None,
    ext_whitelist: str | None = None,
    exclude_path_regex: list[str] | None = None,
) -> FilterOptions:
    """Turn the textual filter settings every frontend collects into options."""
    return FilterOptions(
        ext_blacklist=parse_ext_set(ext_blacklist) if ext_blacklist else set(DEFAULT_EXT_BLACKLIST),
        ext_whitelist=parse_ext_set(ext_whitelist) if ext_whitelist else None,
        path_exclude_regex=[re.compile(x) for x in (exclude_path_regex or [])] or None,
    )

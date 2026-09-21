# SPDX-License-Identifier: GPL-3.0

"""Provider-neutral overlap intelligence for multi-source archive runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import quote

from .config import RunConfig
from .core import DomainStats, ProgressCallback, fingerprint, run
from .output import OutputFormat, UrlRecord
from .providers import SourceName


@dataclass(frozen=True)
class SourceSummary:
    """Archive-source membership counts for one domain."""

    domain: str
    sources: tuple[SourceName, ...]
    union_urls: int
    evidence_records: int
    source_counts: dict[str, int]
    exclusive_counts: dict[str, int]
    shared_urls: int
    overlap_urls: int
    complete: bool = True

    def as_record(self) -> dict:
        return {
            "type": "source_summary",
            "domain": self.domain,
            "sources": list(self.sources),
            "union_urls": self.union_urls,
            "evidence_records": self.evidence_records,
            "source_counts": self.source_counts,
            "exclusive_counts": self.exclusive_counts,
            "shared_urls": self.shared_urls,
            "overlap_urls": self.overlap_urls,
            "complete": self.complete,
        }


@dataclass
class SourceSummaryRunResult:
    stats: list[DomainStats] = field(default_factory=list)
    summaries: dict[str, SourceSummary] = field(default_factory=dict)
    errors: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class _Accumulator:
    """Compact per-domain URL membership using 128-bit URL fingerprints."""

    def __init__(self, sources: tuple[SourceName, ...]):
        self.sources = sources
        self._bits: dict[str, int] = {source: 1 << i for i, source in enumerate(sources)}
        self._masks: dict[str, dict[int, int]] = {}

    def add(self, record: UrlRecord) -> None:
        bit = self._bits.get(record.source)
        if bit is None:
            return
        masks = self._masks.setdefault(record.domain, {})
        fp = fingerprint(record.url)
        masks[fp] = masks.get(fp, 0) | bit

    def summary(self, domain: str, *, complete: bool) -> SourceSummary:
        masks = self._masks.get(domain, {})
        all_mask = 0
        for bit in self._bits.values():
            all_mask |= bit

        source_counts: dict[str, int] = {
            source: sum(bool(mask & self._bits[source]) for mask in masks.values())
            for source in self.sources
        }
        exclusive_counts: dict[str, int] = {
            source: sum(mask == self._bits[source] for mask in masks.values())
            for source in self.sources
        }
        shared_urls = sum(bin(mask).count("1") >= 2 for mask in masks.values())
        overlap_urls = sum(mask == all_mask for mask in masks.values())

        return SourceSummary(
            domain=domain,
            sources=self.sources,
            union_urls=len(masks),
            evidence_records=sum(source_counts.values()),
            source_counts=source_counts,
            exclusive_counts=exclusive_counts,
            shared_urls=shared_urls,
            overlap_urls=overlap_urls,
            complete=complete,
        )


async def run_source_summary(
    cfg: RunConfig,
    *,
    on_progress: ProgressCallback | None = None,
) -> SourceSummaryRunResult:
    """Collect provider membership without letting a bound starve later sources.

    An unlimited summary can use the normal multi-source collector directly.
    With max_results, however, that global budget would be consumed by the
    first provider before later providers are queried. Bounded summaries
    therefore give each selected source the same run-wide budget, preserving a
    useful (explicitly incomplete) sample from every provider.
    """
    accumulator = _Accumulator(cfg.sources)

    if cfg.max_results == 0:
        collection_cfg = replace(
            cfg,
            provenance=True,
            source_summary=False,
            write_files=False,
            analysis=None,
        )
        collected = await run(
            collection_cfg,
            on_record=accumulator.add,
            on_progress=on_progress,
        )
        stats = collected.stats
        errors = collected.errors
    else:
        totals: dict[str, list[int]] = {domain: [0, 0] for domain in cfg.domains}
        complete_by_domain = {domain: True for domain in cfg.domains}
        errors: list[tuple[str, Exception]] = []

        for source in cfg.sources:
            offsets = {domain: tuple(values) for domain, values in totals.items()}

            def source_progress(
                domain: str,
                fetched: int,
                kept: int,
                _offsets: dict[str, tuple[int, int]] = offsets,
            ) -> None:
                if on_progress is None:
                    return
                base_fetched, base_kept = _offsets.get(domain, (0, 0))
                on_progress(domain, base_fetched + fetched, base_kept + kept)

            collection_cfg = replace(
                cfg,
                sources=(source,),
                provenance=False,
                source_summary=False,
                write_files=False,
                analysis=None,
            )
            collected = await run(
                collection_cfg,
                on_record=accumulator.add,
                on_progress=source_progress if on_progress is not None else None,
            )
            by_domain = {stat.domain: stat for stat in collected.stats}
            for domain in cfg.domains:
                stat = by_domain.get(domain)
                if stat is None:
                    complete_by_domain[domain] = False
                    continue
                totals[domain][0] += stat.fetched
                totals[domain][1] += stat.kept
                complete_by_domain[domain] &= stat.complete
            errors.extend(collected.errors)

        stats = [
            DomainStats(
                domain=domain,
                fetched=totals[domain][0],
                kept=totals[domain][1],
                complete=complete_by_domain[domain],
            )
            for domain in cfg.domains
        ]

    complete_by_domain = {stat.domain: stat.complete for stat in stats}
    summaries = {
        domain: accumulator.summary(
            domain,
            complete=complete_by_domain.get(domain, False),
        )
        for domain in cfg.domains
    }
    return SourceSummaryRunResult(
        stats=stats,
        summaries=summaries,
        errors=errors,
    )


def format_source_summary(summary: SourceSummary, fmt: OutputFormat) -> str:
    record = summary.as_record()
    if fmt == "jsonl":
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))

    fields = [
        summary.domain,
        f"union={summary.union_urls}",
        f"shared={summary.shared_urls}",
        f"overlap={summary.overlap_urls}",
    ]
    for source in summary.sources:
        fields.append(f"{source}={summary.source_counts[source]}")
        fields.append(f"{source}_only={summary.exclusive_counts[source]}")
    fields.append(f"complete={str(summary.complete).lower()}")
    return "\t".join(fields)


def _summary_path(outdir: Path, domain: str, fmt: OutputFormat) -> Path:
    safe_domain = quote(domain, safe=".-_")
    return outdir / f"{safe_domain}.sources.{fmt}"


def write_source_summary_files(result: SourceSummaryRunResult, cfg: RunConfig) -> None:
    if not cfg.write_files:
        return
    cfg.outdir.mkdir(parents=True, exist_ok=True)
    for domain in cfg.domains:
        summary = result.summaries.get(domain)
        if summary is None:
            continue
        path = _summary_path(cfg.outdir, domain, cfg.out_format)
        path.write_text(format_source_summary(summary, cfg.out_format) + "\n", encoding="utf-8")

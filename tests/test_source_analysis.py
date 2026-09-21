# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import asyncio
import json

from wayparam import source_analysis
from wayparam.config import RunConfig
from wayparam.core import DomainStats, RunResult
from wayparam.output import UrlRecord
from wayparam.source_analysis import (
    format_source_summary,
    run_source_summary,
    write_source_summary_files,
)


def _cfg(tmp_path, **kw):
    base = dict(
        domains=["example.com"],
        sources=("wayback", "commoncrawl"),
        outdir=tmp_path / "out",
        write_files=False,
        source_summary=True,
    )
    base.update(kw)
    return RunConfig(**base)


def test_source_summary_counts_union_overlap_and_exclusives(tmp_path, monkeypatch):
    async def fake_run(cfg, *, on_record=None, on_progress=None):
        assert cfg.provenance is True
        assert cfg.source_summary is False
        assert cfg.write_files is False
        records = [
            UrlRecord("example.com", "https://example.com/shared?id=FUZZ", "wayback"),
            UrlRecord("example.com", "https://example.com/wb?q=FUZZ", "wayback"),
            UrlRecord("example.com", "https://example.com/shared?id=FUZZ", "commoncrawl"),
            UrlRecord("example.com", "https://example.com/cc?q=FUZZ", "commoncrawl"),
        ]
        for record in records:
            if on_record:
                on_record(record)
        return RunResult(stats=[DomainStats("example.com", 4, 4, True)])

    monkeypatch.setattr(source_analysis, "run", fake_run)
    result = asyncio.run(run_source_summary(_cfg(tmp_path)))

    assert result.ok
    summary = result.summaries["example.com"]
    assert summary.union_urls == 3
    assert summary.evidence_records == 4
    assert summary.source_counts == {"wayback": 2, "commoncrawl": 2}
    assert summary.exclusive_counts == {"wayback": 1, "commoncrawl": 1}
    assert summary.shared_urls == 1
    assert summary.overlap_urls == 1
    assert summary.complete is True


def test_source_summary_is_future_safe_for_three_sources(tmp_path, monkeypatch):
    async def fake_run(cfg, *, on_record=None, on_progress=None):
        records = [
            UrlRecord("example.com", "https://example.com/all?id=FUZZ", "wayback"),
            UrlRecord("example.com", "https://example.com/all?id=FUZZ", "commoncrawl"),
            UrlRecord("example.com", "https://example.com/all?id=FUZZ", "third"),
            UrlRecord("example.com", "https://example.com/two?id=FUZZ", "wayback"),
            UrlRecord("example.com", "https://example.com/two?id=FUZZ", "commoncrawl"),
            UrlRecord("example.com", "https://example.com/wb?id=FUZZ", "wayback"),
        ]
        for record in records:
            if on_record:
                on_record(record)
        return RunResult(stats=[DomainStats("example.com", 6, 6, True)])

    monkeypatch.setattr(source_analysis, "run", fake_run)
    cfg = _cfg(tmp_path, sources=("wayback", "commoncrawl", "third"))
    result = asyncio.run(run_source_summary(cfg))
    summary = result.summaries["example.com"]

    assert summary.union_urls == 3
    assert summary.shared_urls == 2
    assert summary.overlap_urls == 1
    assert summary.exclusive_counts["wayback"] == 1


def test_source_summary_preserves_partial_status_and_errors(tmp_path, monkeypatch):
    async def fake_run(cfg, *, on_record=None, on_progress=None):
        if on_record:
            on_record(UrlRecord("example.com", "https://example.com/?id=FUZZ", "wayback"))
        return RunResult(
            stats=[DomainStats("example.com", 8, 1, False)],
            errors=[("example.com", RuntimeError("commoncrawl: unavailable"))],
        )

    monkeypatch.setattr(source_analysis, "run", fake_run)
    result = asyncio.run(run_source_summary(_cfg(tmp_path)))

    assert not result.ok
    assert result.summaries["example.com"].complete is False
    assert result.summaries["example.com"].source_counts["commoncrawl"] == 0
    assert "unavailable" in str(result.errors[0][1])


def test_source_summary_jsonl_is_stable_and_machine_readable(tmp_path, monkeypatch):
    async def fake_run(cfg, *, on_record=None, on_progress=None):
        if on_record:
            on_record(UrlRecord("example.com", "https://example.com/?id=FUZZ", "wayback"))
            on_record(UrlRecord("example.com", "https://example.com/?id=FUZZ", "commoncrawl"))
        return RunResult(stats=[DomainStats("example.com", 2, 2, True)])

    monkeypatch.setattr(source_analysis, "run", fake_run)
    summary = asyncio.run(run_source_summary(_cfg(tmp_path))).summaries["example.com"]
    record = json.loads(format_source_summary(summary, "jsonl"))

    assert record["type"] == "source_summary"
    assert record["sources"] == ["wayback", "commoncrawl"]
    assert record["union_urls"] == 1
    assert record["overlap_urls"] == 1


def test_source_summary_text_is_compact(tmp_path, monkeypatch):
    async def fake_run(cfg, *, on_record=None, on_progress=None):
        return RunResult(stats=[DomainStats("example.com", 0, 0, True)])

    monkeypatch.setattr(source_analysis, "run", fake_run)
    summary = asyncio.run(run_source_summary(_cfg(tmp_path))).summaries["example.com"]
    text = format_source_summary(summary, "txt")

    assert text.startswith("example.com\tunion=0\tshared=0\toverlap=0")
    assert "wayback_only=0" in text
    assert "commoncrawl_only=0" in text


def test_source_summary_writes_mode_specific_file(tmp_path, monkeypatch):
    async def fake_run(cfg, *, on_record=None, on_progress=None):
        return RunResult(stats=[DomainStats("example.com", 0, 0, True)])

    monkeypatch.setattr(source_analysis, "run", fake_run)
    cfg = _cfg(tmp_path, write_files=True, out_format="jsonl")
    result = asyncio.run(run_source_summary(cfg))
    write_source_summary_files(result, cfg)

    path = cfg.outdir / "example.com.sources.jsonl"
    assert path.is_file()
    assert json.loads(path.read_text().strip())["type"] == "source_summary"



def test_bounded_source_summary_gives_each_provider_its_own_budget(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cfg, *, on_record=None, on_progress=None):
        calls.append((cfg.sources, cfg.max_results))
        source = cfg.sources[0]
        if on_record:
            for i in range(cfg.max_results):
                on_record(
                    UrlRecord(
                        "example.com",
                        f"https://example.com/{source}/{i}?id=FUZZ",
                        source,
                    )
                )
        return RunResult(
            stats=[
                DomainStats(
                    "example.com",
                    fetched=cfg.max_results,
                    kept=cfg.max_results,
                    complete=False,
                )
            ]
        )

    monkeypatch.setattr(source_analysis, "run", fake_run)
    cfg = _cfg(tmp_path, max_results=3)
    result = asyncio.run(run_source_summary(cfg))

    assert calls == [(("wayback",), 3), (("commoncrawl",), 3)]
    summary = result.summaries["example.com"]
    assert summary.source_counts == {"wayback": 3, "commoncrawl": 3}
    assert summary.evidence_records == 6
    assert summary.union_urls == 6
    assert summary.complete is False
    assert result.stats[0].kept == 6


def test_bounded_source_summary_progress_is_monotonic_across_sources(tmp_path, monkeypatch):
    async def fake_run(cfg, *, on_record=None, on_progress=None):
        source = cfg.sources[0]
        if on_record:
            on_record(
                UrlRecord(
                    "example.com",
                    f"https://example.com/{source}?id=FUZZ",
                    source,
                )
            )
        if on_progress:
            on_progress("example.com", 4, 1)
        return RunResult(stats=[DomainStats("example.com", 4, 1, False)])

    monkeypatch.setattr(source_analysis, "run", fake_run)
    progress = []
    cfg = _cfg(tmp_path, max_results=1)
    asyncio.run(
        run_source_summary(
            cfg,
            on_progress=lambda domain, fetched, kept: progress.append(
                (domain, fetched, kept)
            ),
        )
    )

    assert progress == [
        ("example.com", 4, 1),
        ("example.com", 8, 2),
    ]

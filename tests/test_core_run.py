# SPDX-License-Identifier: GPL-3.0

"""End-to-end tests for core.run over a mocked CDX endpoint (no network)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from wayparam import core
from wayparam.config import RunConfig, build_filter_options
from wayparam.http import HttpConfig

PAGE = "\n".join(
    [
        "http://example.com/a.php?id=1&utm_source=news",
        "http://example.com/static/app.js?v=2",
        "http://EXAMPLE.com:80/a.php?id=9",  # same canonical URL as the first
        "http://example.com/plain",  # no query -> dropped by only_params
        "http://example.com/b.php?q=hello",
    ]
)


def _cfg(tmp_path, **kw) -> RunConfig:
    base = dict(
        domains=["example.com"],
        outdir=tmp_path / "out",
        write_files=True,
        http=HttpConfig(retries=0, backoff_base_s=0.0, max_backoff_s=0.0),
        filters=build_filter_options(),
    )
    base.update(kw)
    return RunConfig(**base)


def _run(cfg, page=PAGE, on_record=None):
    """Drive core.run against a single-page mocked CDX response."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=page))
    real_client = httpx.AsyncClient

    def patched(**kwargs):
        kwargs.pop("proxy", None)
        kwargs.pop("proxies", None)
        return real_client(transport=transport, **kwargs)

    core.httpx.AsyncClient = patched  # type: ignore[assignment]
    try:
        return asyncio.run(core.run(cfg, on_record=on_record))
    finally:
        core.httpx.AsyncClient = real_client  # type: ignore[assignment]


def test_dedupes_normalizes_and_filters(tmp_path):
    seen = []
    result = _run(_cfg(tmp_path), on_record=seen.append)

    assert result.ok
    urls = [r.url for r in seen]
    # .js is boring; /plain has no query; the two a.php entries collapse into one
    assert urls == [
        "http://example.com/a.php?id=FUZZ",
        "http://example.com/b.php?q=FUZZ",
    ]
    assert result.stats[0].fetched == 5
    assert result.stats[0].kept == 2


def test_writes_one_file_per_domain(tmp_path):
    cfg = _cfg(tmp_path)
    _run(cfg)

    written = (cfg.outdir / "example.com.txt").read_text().splitlines()
    assert written == [
        "http://example.com/a.php?id=FUZZ",
        "http://example.com/b.php?q=FUZZ",
    ]


def test_no_files_leaves_no_output_directory(tmp_path):
    cfg = _cfg(tmp_path, write_files=False)
    _run(cfg)
    assert not cfg.outdir.exists()


def test_broken_pipe_from_callback_is_not_swallowed(tmp_path):
    def boom(_rec):
        raise BrokenPipeError(32, "Broken pipe")

    with pytest.raises(BrokenPipeError):
        _run(_cfg(tmp_path, write_files=False), on_record=boom)


def test_failing_domain_is_reported_not_raised(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    real_client = httpx.AsyncClient
    core.httpx.AsyncClient = lambda **kw: real_client(transport=transport, **kw)  # type: ignore[assignment]
    try:
        result = asyncio.run(core.run(_cfg(tmp_path, write_files=False)))
    finally:
        core.httpx.AsyncClient = real_client  # type: ignore[assignment]

    assert not result.ok
    assert result.errors[0][0] == "example.com"
    assert result.stats == []

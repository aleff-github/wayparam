# SPDX-License-Identifier: GPL-3.0

"""End-to-end tests for core.run over a mocked CDX endpoint (no network)."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys

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
    # The domain still reports what it produced before dying -- here nothing,
    # but the entry has to exist and be flagged incomplete.
    assert [(s.domain, s.kept, s.complete) for s in result.stats] == [("example.com", 0, False)]


PAGED = {
    1: "http://example.com/a.php?id=1\nhttp://example.com/b.php?id=2\nRESUME1\n",
    2: "http://example.com/c.php?id=3\n",
}


def _run_paged(cfg, second_page, on_record=None):
    """Two CDX pages, where the caller decides what the second one does."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "resumeKey" not in dict(request.url.params):
            return httpx.Response(200, text=PAGED[1])
        calls["n"] += 1
        return second_page(request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    core.httpx.AsyncClient = lambda **kw: real_client(transport=transport, **kw)  # type: ignore[assignment]
    try:
        return asyncio.run(core.run(cfg, on_record=on_record))
    finally:
        core.httpx.AsyncClient = real_client  # type: ignore[assignment]


def test_a_domain_that_dies_midway_keeps_the_stats_it_earned(tmp_path):
    """Failing on page 2 must not throw away what page 1 already produced."""
    result = _run_paged(
        _cfg(tmp_path, write_files=False),
        lambda request: httpx.Response(500, text="boom"),
    )

    assert not result.ok
    assert result.errors[0][0] == "example.com"
    (st,) = result.stats
    assert (st.fetched, st.kept, st.complete) == (2, 2, False)


def test_a_completed_domain_is_marked_complete(tmp_path):
    result = _run_paged(
        _cfg(tmp_path, write_files=False),
        lambda request: httpx.Response(200, text=PAGED[2]),
    )
    assert result.ok
    (st,) = result.stats
    assert (st.fetched, st.kept, st.complete) == (3, 3, True)


def test_max_results_caps_the_whole_run(tmp_path):
    seen = []
    result = _run(_cfg(tmp_path, write_files=False, max_results=1), on_record=seen.append)

    assert len(seen) == 1
    assert result.ok  # a budget stop is not an error
    (st,) = result.stats
    assert st.kept == 1
    assert st.complete is False


def test_max_results_is_shared_across_domains(tmp_path):
    seen = []
    cfg = _cfg(tmp_path, domains=["a.example", "b.example"], write_files=False, max_results=3)
    _run(cfg, on_record=seen.append)
    # Two URLs survive per domain, so an unbounded run would emit four.
    assert len(seen) == 3


def test_zero_max_results_means_unlimited(tmp_path):
    seen = []
    _run(_cfg(tmp_path, write_files=False, max_results=0), on_record=seen.append)
    assert len(seen) == 2


def test_progress_is_reported_while_a_domain_runs(tmp_path, monkeypatch):
    # One update per row, so a five-row page is observable in a unit test.
    monkeypatch.setattr(core, "_PROGRESS_EVERY", 1)
    seen = []

    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=PAGE))
    real_client = httpx.AsyncClient
    core.httpx.AsyncClient = lambda **kw: real_client(transport=transport, **kw)  # type: ignore[assignment]
    try:
        asyncio.run(
            core.run(
                _cfg(tmp_path, write_files=False),
                on_progress=lambda d, f, k: seen.append((d, f, k)),
            )
        )
    finally:
        core.httpx.AsyncClient = real_client  # type: ignore[assignment]

    assert [f for _, f, _ in seen] == [1, 2, 3, 4, 5]
    assert {d for d, _, _ in seen} == {"example.com"}


# ---- the dedup fingerprint -----------------------------------------------


def test_fingerprint_is_stable_across_processes():
    """Deliberately not the built-in hash(), which is randomised per process.

    A fingerprint that changes between runs would quietly rule out ever
    persisting or comparing these, so pin the actual value.
    """
    assert core.fingerprint("https://example.com/a.php?id=FUZZ") == int.from_bytes(
        hashlib.blake2b(b"https://example.com/a.php?id=FUZZ", digest_size=16).digest(),
        "big",
    )
    # Same input, same answer -- in a subprocess, where hash() would differ.
    # sys.path is handed over explicitly rather than relying on PYTHONPATH
    # being inherited: the Debian build runs this suite in a sandbox.
    code = (
        f"import sys; sys.path[:0] = {sys.path!r}\n"
        "from wayparam.core import fingerprint; print(fingerprint('x'))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert int(out.stdout) == core.fingerprint("x")


def test_fingerprint_is_128_bits_wide():
    """Narrower would make a collision -- a silently dropped URL -- thinkable."""
    assert core.fingerprint("https://example.com/?a=FUZZ") < 2**128
    widths = {core.fingerprint(f"https://example.com/{i}?a=FUZZ").bit_length() for i in range(200)}
    assert max(widths) > 120  # not accidentally truncated to 64


def test_distinct_urls_get_distinct_fingerprints():
    urls = [f"https://example.com/{i}/p.php?id=FUZZ&n={i}" for i in range(5000)]
    assert len({core.fingerprint(u) for u in urls}) == len(urls)


def test_dedup_survives_urls_that_are_not_ascii(tmp_path):
    """The digest is taken over UTF-8 bytes, so non-ASCII must not raise."""
    page = "\n".join(
        [
            "https://example.com/caffè?q=1",
            "https://example.com/caffè?q=2",  # same canonical form
            "https://example.com/日本?q=3",
        ]
    )
    seen = []
    _run(_cfg(tmp_path, write_files=False), page=page, on_record=seen.append)
    assert len(seen) == 2


def test_the_cdx_iterator_is_closed_while_the_client_is_still_open(tmp_path, monkeypatch):
    """Regression: an early exit left the generator suspended.

    It was then finalised at event-loop shutdown -- by which point the httpx
    client it streams from had already been closed -- and the teardown failed
    with 'aclose(): asynchronous generator is already running'. Only a real
    connection pool shows the symptom, so pin the invariant instead: the
    iterator must be closed while the client is still usable.
    """
    state: dict[str, bool] = {}

    async def fake_iter(domain, *, client, http_config, rate_limiter, opt):
        try:
            for i in range(100):
                yield f"https://example.com/p{i}?a=1"
        finally:
            state["client_still_open"] = not client.is_closed

    monkeypatch.setattr(core, "iter_original_urls", fake_iter)

    seen = []
    # The budget stops the walk long before the iterator runs out.
    result = _run(_cfg(tmp_path, write_files=False, max_results=2), on_record=seen.append)

    assert len(seen) == 2
    assert result.stats[0].complete is False
    assert state["client_still_open"] is True


def test_the_cdx_iterator_is_closed_when_the_consumer_goes_away(tmp_path, monkeypatch):
    """The same has to hold when a closed pipe unwinds the run."""
    state: dict[str, bool] = {}

    async def fake_iter(domain, *, client, http_config, rate_limiter, opt):
        try:
            for i in range(100):
                yield f"https://example.com/p{i}?a=1"
        finally:
            state["client_still_open"] = not client.is_closed

    monkeypatch.setattr(core, "iter_original_urls", fake_iter)

    def boom(_rec):
        raise BrokenPipeError(32, "Broken pipe")

    with pytest.raises(BrokenPipeError):
        _run(_cfg(tmp_path, write_files=False), on_record=boom)

    assert state["client_still_open"] is True

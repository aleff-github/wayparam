import asyncio

import httpx

from wayparam.filters import DEFAULT_EXT_BLACKLIST, FilterOptions, is_boring
from wayparam.http import HttpConfig, get_text, iter_lines
from wayparam.normalize import NormalizeOptions, canonicalize_url
from wayparam.wayback import CdxOptions, iter_original_urls


def test_get_text_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="rate limited")
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            cfg = HttpConfig(timeout_s=5, retries=2, backoff_base_s=0.0, max_backoff_s=0.0)
            txt = await get_text(
                client,
                "https://web.archive.org/cdx/search/cdx",
                params=[("url", "example.com")],
                config=cfg,
            )
            return txt

    txt = asyncio.run(run())
    assert txt == "ok"
    assert calls["n"] == 2


def test_get_text_raises_after_retries_includes_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            cfg = HttpConfig(timeout_s=5, retries=1, backoff_base_s=0.0, max_backoff_s=0.0)
            try:
                await get_text(
                    client,
                    "https://web.archive.org/cdx/search/cdx",
                    params=[("url", "example.com")],
                    config=cfg,
                )
            except RuntimeError as e:
                return str(e)
        return ""

    msg = asyncio.run(run())
    assert "failed after retries" in msg.lower()
    assert "status=503" in msg  # from enhanced error detail


def test_iter_original_urls_paginates_with_resume_key():
    """Explicitly in resume mode: `auto` would switch to the block API here."""

    # Page 1 returns two urls + resume key, page 2 returns one url no resume key
    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        if "resumeKey" not in q:
            body = "http://a.example/path?x=1\nhttp://b.example/path?y=2\nresumeKey: RK1\n"
            return httpx.Response(200, text=body)
        assert q["resumeKey"] == "RK1"
        body = "http://c.example/path?z=3\n"
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            cfg = HttpConfig(timeout_s=5, retries=0, backoff_base_s=0.0, max_backoff_s=0.0)
            opt = CdxOptions(include_subdomains=False, collapse=None, limit=10, pagination="resume")
            out = []
            async for u in iter_original_urls(
                "example.com", client=client, http_config=cfg, rate_limiter=None, opt=opt
            ):
                out.append(u)
            return out

    urls = asyncio.run(run())
    assert urls == [
        "http://a.example/path?x=1",
        "http://b.example/path?y=2",
        "http://c.example/path?z=3",
    ]


def test_pipeline_filters_boring_and_normalizes_params():
    # Returns: boring png, url without params, and interesting url with params
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            "https://example.com/static/logo.png\n"
            "https://example.com/noquery\n"
            "https://example.com/search?q=term&lang=en\n"
        )
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            cfg = HttpConfig(timeout_s=5, retries=0, backoff_base_s=0.0, max_backoff_s=0.0)
            opt = CdxOptions(collapse=None, limit=10)
            filt = FilterOptions(ext_blacklist=set(DEFAULT_EXT_BLACKLIST))
            norm = NormalizeOptions(
                placeholder="FUZZ", keep_values=False, only_params=True, drop_tracking=False
            )

            kept = []
            async for raw in iter_original_urls(
                "example.com", client=client, http_config=cfg, rate_limiter=None, opt=opt
            ):
                if is_boring(raw, filt):
                    continue
                canon = canonicalize_url(raw, norm)
                if canon:
                    kept.append(canon)
            return kept

    kept = asyncio.run(run())
    assert kept == ["https://example.com/search?lang=FUZZ&q=FUZZ"]  # sorted params


def test_get_text_retries_a_truncated_response():
    """The CDX API truncates large chunked bodies; httpx reports that as
    RemoteProtocolError, which is not a NetworkError and used to escape the
    retry loop as a permanent failure."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("incomplete chunked read", request=request)
        return httpx.Response(200, text="http://a.example/p?x=1\n")

    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            cfg = HttpConfig(timeout_s=5, retries=2, backoff_base_s=0.0, max_backoff_s=0.0)
            return await get_text(
                client, "https://web.archive.org/cdx/search/cdx", params=[], config=cfg
            )

    assert asyncio.run(run()) == "http://a.example/p?x=1\n"
    assert calls["n"] == 2


def test_get_text_does_not_wait_after_the_last_429():
    """Retry-After on the final attempt is a delay before a certain failure."""
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "10"}, text="rate limited")

    transport = httpx.MockTransport(handler)

    async def run():
        import wayparam.http as http_mod

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        real_sleep = http_mod.asyncio.sleep
        http_mod.asyncio.sleep = fake_sleep  # type: ignore[assignment]
        try:
            async with httpx.AsyncClient(transport=transport) as client:
                cfg = HttpConfig(timeout_s=5, retries=1, backoff_base_s=0.0, max_backoff_s=10.0)
                try:
                    await get_text(
                        client, "https://web.archive.org/cdx/search/cdx", params=[], config=cfg
                    )
                except RuntimeError as e:
                    return str(e)
        finally:
            http_mod.asyncio.sleep = real_sleep  # type: ignore[assignment]
        return ""

    msg = asyncio.run(run())
    assert "status=429" in msg
    # retries=1 -> two attempts, but only the first one is followed by a wait.
    assert slept == [10]


# ---- streamed responses --------------------------------------------------


def _drain(handler, cfg=None):
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return [
                line
                async for line in iter_lines(
                    client,
                    "https://web.archive.org/cdx/search/cdx",
                    params=[("url", "example.com")],
                    config=cfg or HttpConfig(retries=2, backoff_base_s=0.0, max_backoff_s=0.0),
                )
            ]

    return asyncio.run(run())


def test_iter_lines_yields_non_empty_lines():
    def handler(request):
        return httpx.Response(200, text="http://a.example/?x=1\n\n  \nhttp://b.example/?y=2\n")

    assert _drain(handler) == ["http://a.example/?x=1", "http://b.example/?y=2"]


def test_iter_lines_does_not_buffer_the_whole_body():
    """The caller must see early lines before the body has finished arriving."""
    seen_before_end = []
    finished = []

    async def body():
        for i in range(3):
            yield f"http://p{i}.example/?x=1\n".encode()
        finished.append(True)

    def handler(request):
        return httpx.Response(200, content=body())

    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            async for line in iter_lines(
                client,
                "https://web.archive.org/cdx/search/cdx",
                config=HttpConfig(retries=0, backoff_base_s=0.0, max_backoff_s=0.0),
            ):
                seen_before_end.append((line, bool(finished)))

    asyncio.run(run())
    # The first line arrived while the generator had not been exhausted yet.
    assert seen_before_end[0] == ("http://p0.example/?x=1", False)
    assert [ln for ln, _ in seen_before_end] == [f"http://p{i}.example/?x=1" for i in range(3)]


def test_a_body_that_dies_midway_is_retried_without_repeating_lines():
    """A retry restarts the request, so already-delivered lines must be skipped."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:

            async def broken():
                yield b"http://a.example/?x=1\nhttp://b.example/?x=2\n"
                raise httpx.ReadError("connection dropped")

            return httpx.Response(200, content=broken())
        return httpx.Response(
            200,
            text="http://a.example/?x=1\nhttp://b.example/?x=2\nhttp://c.example/?x=3\n",
        )

    assert _drain(handler) == [
        "http://a.example/?x=1",
        "http://b.example/?x=2",
        "http://c.example/?x=3",
    ]
    assert attempts["n"] == 2


def test_iter_lines_retries_a_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, text="http://a.example/?x=1\n")

    assert _drain(handler) == ["http://a.example/?x=1"]
    assert calls["n"] == 2


def test_iter_lines_raises_after_retries_with_the_status():
    def handler(request):
        return httpx.Response(503, text="unavailable")

    try:
        _drain(handler, HttpConfig(retries=1, backoff_base_s=0.0, max_backoff_s=0.0))
    except RuntimeError as e:
        assert "failed after retries" in str(e).lower()
        assert "status=503" in str(e)
    else:
        raise AssertionError("expected RuntimeError")

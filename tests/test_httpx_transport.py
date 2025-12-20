import asyncio

import httpx

from wayparam.filters import DEFAULT_EXT_BLACKLIST, FilterOptions, is_boring
from wayparam.http import HttpConfig, get_text
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
            opt = CdxOptions(include_subdomains=False, collapse=None, limit=10)
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

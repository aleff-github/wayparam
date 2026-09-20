# SPDX-License-Identifier: GPL-3.0

"""How wayparam walks a multi-page CDX result.

The endpoint paginates two ways and only one of them is lossless with
`collapse` on, so which requests go out -- and with which parameters -- is
behaviour worth pinning down.
"""

from __future__ import annotations

import asyncio

import httpx

from wayparam.http import HttpConfig
from wayparam.wayback import (
    CdxOptions,
    _build_num_pages_params,
    iter_captures,
    iter_original_urls,
    parse_num_pages,
)

CFG = HttpConfig(timeout_s=5, retries=0, backoff_base_s=0.0, max_backoff_s=0.0)


def _collect(handler, opt: CdxOptions) -> tuple[list[str], list[dict]]:
    """Run the generator against a mock endpoint, returning urls and requests."""
    seen: list[dict] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return handler(request)

    async def go() -> list[str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(spy)) as client:
            return [
                u
                async for u in iter_original_urls(
                    "example.com", client=client, http_config=CFG, rate_limiter=None, opt=opt
                )
            ]

    return asyncio.run(go()), seen


# ---- the page count ------------------------------------------------------


def test_parse_num_pages():
    assert parse_num_pages("9\n") == 9
    assert parse_num_pages(" 0 ") == 0
    # "-" is how the API says pagination is not available for this query.
    assert parse_num_pages("-") is None
    assert parse_num_pages("") is None
    assert parse_num_pages("http://example.com/") is None


def test_the_page_count_request_must_not_ask_for_a_row_format():
    """Regression: with output=txt&fl=original the endpoint answers '-' always.

    That reads as 'pagination unavailable' and silently sends every run down
    the lossy path, which is exactly the bug this mode exists to avoid.
    """
    params = dict(_build_num_pages_params("example.com", CdxOptions()))
    assert "output" not in params
    assert "fl" not in params
    assert params["showNumPages"] == "true"
    # The row filters still have to be there, or the count describes another query.
    assert params["url"] == "example.com"
    assert params["collapse"] == "urlkey"


# ---- auto ----------------------------------------------------------------


def test_auto_stays_on_one_request_when_the_result_fits():
    """No resumeKey means no page boundary, so nothing can be lost."""

    def handler(request):
        return httpx.Response(200, text="http://a.example/?x=1\nhttp://b.example/?y=2\n")

    urls, requests = _collect(handler, CdxOptions())

    assert urls == ["http://a.example/?x=1", "http://b.example/?y=2"]
    assert len(requests) == 1
    assert "showResumeKey" in requests[0]


def test_auto_switches_to_blocks_when_the_result_spans_pages():
    def handler(request):
        q = dict(request.url.params)
        if "showNumPages" in q:
            return httpx.Response(200, text="2\n")
        if "page" in q:
            return httpx.Response(200, text=f"http://p{q['page']}.example/?x=1\n")
        return httpx.Response(200, text="http://probe.example/?x=1\nRESUME\n")

    urls, requests = _collect(handler, CdxOptions())

    # The probe's rows are discarded: everything yielded comes from the block
    # pass, so the counts describe one consistent walk.
    assert urls == ["http://p0.example/?x=1", "http://p1.example/?x=1"]
    assert [("showNumPages" in r, r.get("page")) for r in requests] == [
        (False, None),  # probe
        (True, None),  # page count
        (False, "0"),
        (False, "1"),
    ]


def test_auto_falls_back_to_resume_when_blocks_are_unavailable(caplog):
    def handler(request):
        q = dict(request.url.params)
        if "showNumPages" in q:
            return httpx.Response(200, text="-\n")
        if "resumeKey" in q:
            return httpx.Response(200, text="http://second.example/?x=1\n")
        return httpx.Response(200, text="http://first.example/?x=1\nRESUME\n")

    urls, _ = _collect(handler, CdxOptions())

    assert urls == ["http://first.example/?x=1", "http://second.example/?x=1"]
    assert "block pagination API is unavailable" in caplog.text


# ---- explicit modes ------------------------------------------------------


def test_blocks_mode_never_sends_a_resume_key():
    def handler(request):
        q = dict(request.url.params)
        if "showNumPages" in q:
            return httpx.Response(200, text="3\n")
        return httpx.Response(200, text=f"http://p{q['page']}.example/?x=1\n")

    urls, requests = _collect(handler, CdxOptions(pagination="blocks"))

    assert len(urls) == 3
    assert not any("resumeKey" in r or "showResumeKey" in r for r in requests)
    assert all(r["pageSize"] == "100" for r in requests)


def test_resume_mode_warns_that_collapse_loses_rows(caplog):
    def handler(request):
        if "resumeKey" in dict(request.url.params):
            return httpx.Response(200, text="http://b.example/?x=1\n")
        return httpx.Response(200, text="http://a.example/?x=1\nRESUME\n")

    urls, _ = _collect(handler, CdxOptions(pagination="resume"))

    assert urls == ["http://a.example/?x=1", "http://b.example/?x=1"]
    assert "drops one URL per page boundary" in caplog.text


def test_resume_mode_stays_quiet_without_collapse(caplog):
    def handler(request):
        if "resumeKey" in dict(request.url.params):
            return httpx.Response(200, text="http://b.example/?x=1\n")
        return httpx.Response(200, text="http://a.example/?x=1\nRESUME\n")

    _collect(handler, CdxOptions(pagination="resume", collapse=None))
    assert "page boundary" not in caplog.text


def test_an_unknown_mode_behaves_like_auto():
    def handler(request):
        return httpx.Response(200, text="http://a.example/?x=1\n")

    urls, requests = _collect(handler, CdxOptions(pagination="nonsense"))
    assert urls == ["http://a.example/?x=1"]
    assert len(requests) == 1


def test_capture_iterator_requests_and_parses_metadata():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            text="20240102030405 200 text/html https://example.com/path?q=1\n",
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [
                capture
                async for capture in iter_captures(
                    "example.com",
                    client=client,
                    http_config=CFG,
                    rate_limiter=None,
                    opt=CdxOptions(metadata=True),
                )
            ]

    captures = asyncio.run(go())
    assert len(captures) == 1
    assert captures[0].timestamp == "20240102030405"
    assert captures[0].original == "https://example.com/path?q=1"
    assert seen[0]["fl"] == "timestamp,statuscode,mimetype,original"


def test_original_url_iterator_keeps_url_only_contract_with_metadata_option():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, text="https://example.com/?q=1\n")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [
                url
                async for url in iter_original_urls(
                    "example.com",
                    client=client,
                    http_config=CFG,
                    rate_limiter=None,
                    opt=CdxOptions(metadata=True),
                )
            ]

    assert asyncio.run(go()) == ["https://example.com/?q=1"]
    assert seen[0]["fl"] == "original"


def test_metadata_auto_switches_to_block_pages():
    requests = []

    def handler(request):
        query = dict(request.url.params)
        requests.append(query)
        if "showNumPages" in query:
            return httpx.Response(200, text="2\n")
        if "page" in query:
            page = query["page"]
            return httpx.Response(
                200,
                text=f"20240{page}01000000 200 text/html https://example.com/p{page}?id=1\n",
            )
        return httpx.Response(
            200,
            text=("20230101000000 200 text/html https://example.com/probe?id=1\nRESUME\n"),
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [
                capture
                async for capture in iter_captures(
                    "example.com",
                    client=client,
                    http_config=CFG,
                    rate_limiter=None,
                    opt=CdxOptions(metadata=True),
                )
            ]

    captures = asyncio.run(go())
    assert [capture.original for capture in captures] == [
        "https://example.com/p0?id=1",
        "https://example.com/p1?id=1",
    ]
    assert [query.get("page") for query in requests if "page" in query] == ["0", "1"]


def test_metadata_blocks_fall_back_to_resume_when_page_count_is_unavailable(caplog):
    def handler(request):
        query = dict(request.url.params)
        if "showNumPages" in query:
            return httpx.Response(200, text="-\n")
        if "resumeKey" in query:
            return httpx.Response(
                200,
                text="20240201000000 200 text/html https://example.com/b?id=2\n",
            )
        return httpx.Response(
            200,
            text=("20240101000000 200 text/html https://example.com/a?id=1\nRESUME\n"),
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [
                capture
                async for capture in iter_captures(
                    "example.com",
                    client=client,
                    http_config=CFG,
                    rate_limiter=None,
                    opt=CdxOptions(metadata=True, pagination="blocks"),
                )
            ]

    captures = asyncio.run(go())
    assert [capture.timestamp for capture in captures] == [
        "20240101000000",
        "20240201000000",
    ]
    assert "block pagination API is unavailable" in caplog.text

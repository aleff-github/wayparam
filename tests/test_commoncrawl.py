# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import wayparam.providers.commoncrawl as commoncrawl_module

from wayparam.http import HttpConfig
from wayparam.providers.commoncrawl import (
    CommonCrawlOptions,
    CommonCrawlProvider,
    parse_index_record,
    parse_page_count,
)
from wayparam.wayback import CdxOptions

CFG = HttpConfig(timeout_s=5, retries=0, backoff_base_s=0.0, max_backoff_s=0.0)


def _collect(handler, *, options=None, query=None):
    requests: list[httpx.Request] = []

    def spy(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    async def go():
        provider = CommonCrawlProvider(
            options=options or CommonCrawlOptions(rps=0),
            query=query or CdxOptions(),
            http=CFG,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(spy)) as client:
            return [record async for record in provider.iter_urls("example.com", client=client)]

    return asyncio.run(go()), requests


def test_parse_page_count():
    assert parse_page_count('{"blocks": 9, "pages": 2, "pageSize": 5}') == 2
    assert parse_page_count('{"blocks": 0, "pages": 0, "pageSize": 5}') == 0
    assert parse_page_count("") == 0
    assert parse_page_count("not json") is None
    assert parse_page_count('{"pages": "3"}') == 3
    assert parse_page_count('{"pages": true}') is None


def test_parse_index_record():
    record = parse_index_record(
        json.dumps(
            {
                "url": "https://example.com/a?id=1",
                "timestamp": "20260915123456",
                "status": "200",
                "mime": "text/html",
            }
        )
    )
    assert record is not None
    assert record.original == "https://example.com/a?id=1"
    assert record.source == "commoncrawl"
    assert record.timestamp == "20260915123456"
    assert record.status_code == "200"
    assert record.mime_type == "text/html"


@pytest.mark.parametrize("line", ["", "[]", '{"status":"200"}', "not-json"])
def test_parse_index_record_rejects_bad_rows(line):
    assert parse_index_record(line) is None


def test_latest_index_is_discovered_then_paged():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collinfo.json":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "CC-MAIN-2026-39",
                        "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-39-index",
                    }
                ],
            )
        query = dict(request.url.params)
        if "showNumPages" in query:
            return httpx.Response(200, json={"blocks": 6, "pages": 2, "pageSize": 5})
        page = int(query["page"])
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "url": f"https://example.com/p{page}?id={page}",
                    "timestamp": f"2026091{page}000000",
                    "status": "200",
                    "mime": "text/html",
                }
            )
            + "\n",
        )

    records, requests = _collect(handler)

    assert [record.original for record in records] == [
        "https://example.com/p0?id=0",
        "https://example.com/p1?id=1",
    ]
    assert requests[0].url.path == "/collinfo.json"
    page_queries = [
        dict(request.url.params) for request in requests if "page" in request.url.params
    ]
    assert [query["page"] for query in page_queries] == ["0", "1"]
    assert all(query["output"] == "json" for query in page_queries)
    assert all(query["pageSize"] == "5" for query in page_queries)


def test_explicit_indexes_skip_collection_discovery_and_dedupe_indexes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/collinfo.json"
        query = dict(request.url.params)
        if "showNumPages" in query:
            return httpx.Response(200, json={"blocks": 1, "pages": 1, "pageSize": 5})
        return httpx.Response(
            200,
            text=json.dumps({"url": "https://example.com/?q=1", "status": "200"}) + "\n",
        )

    records, requests = _collect(
        handler,
        options=CommonCrawlOptions(
            indexes=("CC-MAIN-2026-34", "CC-MAIN-2026-34"),
            rps=0,
        ),
    )

    assert len(records) == 1
    assert {request.url.path for request in requests} == {"/CC-MAIN-2026-34-index"}


def test_commoncrawl_query_reuses_shared_scope_and_dates():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params)
        seen.append(query)
        if "showNumPages" in query:
            return httpx.Response(200, json={"blocks": 0, "pages": 0, "pageSize": 7})
        raise AssertionError("no data page expected")

    records, _ = _collect(
        handler,
        options=CommonCrawlOptions(
            indexes=("CC-MAIN-2026-39",),
            page_size=7,
            filters=("status:200",),
            rps=0,
        ),
        query=CdxOptions(
            include_subdomains=True,
            collapse=None,
            from_ts="2026",
            to_ts="20260930",
        ),
    )

    assert records == []
    query = seen[0]
    assert query["matchType"] == "domain"
    assert query["from"] == "2026"
    assert query["to"] == "20260930"
    assert query["filter"] == "status:200"
    assert "collapse" not in query


def test_404_means_no_captures_not_a_failed_run():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collinfo.json":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "CC-MAIN-2026-39",
                        "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-39-index",
                    }
                ],
            )
        return httpx.Response(404, text='{"error":"No Captures found"}')

    records, _ = _collect(handler)
    assert records == []


def test_default_commoncrawl_user_agent_is_descriptive():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        if request.url.path == "/collinfo.json":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "CC-MAIN-2026-39",
                        "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-39-index",
                    }
                ],
            )
        return httpx.Response(200, json={"blocks": 0, "pages": 0, "pageSize": 5})

    _collect(handler)
    assert seen
    assert all(value.startswith("wayparam/") for value in seen)
    assert all("Common Crawl index client" in value for value in seen)


def test_invalid_explicit_index_is_rejected():
    async def go():
        provider = CommonCrawlProvider(
            options=CommonCrawlOptions(indexes=("not-an-index",), rps=0),
            query=CdxOptions(),
            http=CFG,
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(500))
        ) as client:
            return [record async for record in provider.iter_urls("example.com", client=client)]

    with pytest.raises(ValueError, match="invalid Common Crawl index"):
        asyncio.run(go())


def test_early_provider_close_closes_nested_commoncrawl_stream(monkeypatch):
    state = {"closed": False}

    class ClosableLines:
        def __init__(self):
            self._done = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._done:
                raise StopAsyncIteration
            self._done = True
            return json.dumps(
                {
                    "url": "https://example.com/?id=1",
                    "timestamp": "20260915123456",
                    "status": "200",
                    "mime": "text/html",
                }
            )

        async def aclose(self):
            state["closed"] = True

    def fake_iter_lines(*args, **kwargs):
        return ClosableLines()

    monkeypatch.setattr(commoncrawl_module, "iter_lines", fake_iter_lines)

    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params)
        if "showNumPages" in query:
            return httpx.Response(200, json={"blocks": 1, "pages": 1, "pageSize": 5})
        raise AssertionError("data rows should come from the patched streaming iterator")

    async def go():
        provider = CommonCrawlProvider(
            options=CommonCrawlOptions(indexes=("CC-MAIN-2026-39",), rps=0),
            query=CdxOptions(),
            http=CFG,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            records = provider.iter_urls("example.com", client=client)
            first = await anext(records)
            assert first.original == "https://example.com/?id=1"
            await records.aclose()
            assert state["closed"] is True

    asyncio.run(go())

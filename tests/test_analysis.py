# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import asyncio
import json

import httpx

from wayparam import analysis
from wayparam.analysis import (
    DomainHistory,
    HistoryRunResult,
    format_record,
    records_for,
    run_history,
    write_analysis_files,
)
from wayparam.config import RunConfig, build_filter_options
from wayparam.http import HttpConfig
from wayparam.wayback import CaptureRecord

PAGE = "\n".join(
    [
        "20200101000000 200 text/html https://example.com/item?id=1&lang=en",
        "20210101000000 302 text/html https://example.com/item?id=2&lang=en",
        "20220101000000 200 application/json https://example.com/api?q=test",
    ]
)


def _history() -> DomainHistory:
    history = DomainHistory(domain="example.com")
    history.fetched = 3
    history.add(
        "https://example.com/item?id=FUZZ&lang=FUZZ",
        CaptureRecord(
            original="https://example.com/item?id=1&lang=en",
            timestamp="20200101000000",
            status_code="200",
            mime_type="text/html",
        ),
    )
    history.add(
        "https://example.com/item?id=FUZZ&lang=FUZZ",
        CaptureRecord(
            original="https://example.com/item?id=2&lang=en",
            timestamp="20210101000000",
            status_code="302",
            mime_type="text/html",
        ),
    )
    history.add(
        "https://example.com/api?q=FUZZ",
        CaptureRecord(
            original="https://example.com/api?q=test",
            timestamp="20220101000000",
            status_code="200",
            mime_type="application/json",
        ),
    )
    return history


def test_history_aggregates_capture_dates_status_and_mime():
    history = _history()
    records = records_for(history, "history")

    item = next(record for record in records if "/item?" in record["url"])
    assert item["captures"] == 2
    assert item["first_seen"] == "20200101000000"
    assert item["last_seen"] == "20210101000000"
    assert item["status_codes"] == {"200": 1, "302": 1}
    assert item["mime_types"] == {"text/html": 2}


def test_parameter_view_counts_endpoints_and_captures():
    records = {record["parameter"]: record for record in records_for(_history(), "params")}

    assert records["id"]["endpoints"] == 1
    assert records["id"]["captures"] == 2
    assert records["lang"]["captures"] == 2
    assert records["q"]["captures"] == 1


def test_summary_is_one_record_per_domain():
    (record,) = records_for(_history(), "summary")

    assert record["captures"] == 3
    assert record["accepted_captures"] == 3
    assert record["unique_urls"] == 2
    assert record["unique_parameters"] == 3
    assert record["first_seen"] == "20200101000000"
    assert record["last_seen"] == "20220101000000"
    assert record["status_codes"] == {"200": 2, "302": 1}


def test_timeline_year_view_reports_activity_and_first_seen_counts():
    records = {record["period"]: record for record in records_for(_history(), "timeline")}

    assert records["2020"] == {
        "type": "timeline",
        "domain": "example.com",
        "period": "2020",
        "captures": 1,
        "unique_urls": 1,
        "new_urls": 1,
        "unique_parameters": 2,
        "new_parameters": 2,
    }
    assert records["2021"]["captures"] == 1
    assert records["2021"]["unique_urls"] == 1
    assert records["2021"]["new_urls"] == 0
    assert records["2021"]["new_parameters"] == 0
    assert records["2022"]["unique_urls"] == 1
    assert records["2022"]["new_urls"] == 1
    assert records["2022"]["new_parameters"] == 1


def test_timeline_month_granularity_is_deterministic():
    records = records_for(_history(), "timeline", timeline_granularity="month")

    assert [record["period"] for record in records] == ["202001", "202101", "202201"]
    assert all(record["captures"] == 1 for record in records)


def test_text_timeline_output_is_tab_separated():
    record = records_for(_history(), "timeline")[0]
    encoded = format_record(record, "txt")
    assert encoded.split("\t") == ["2020", "1", "1", "1", "2", "2"]


def _topology_history() -> DomainHistory:
    history = DomainHistory(domain="example.com")
    observations = [
        (
            "https://example.com/item?id=FUZZ&lang=FUZZ",
            "20200101000000",
        ),
        (
            "https://example.com/item?id=FUZZ&lang=FUZZ",
            "20210101000000",
        ),
        (
            "http://example.com/item?id=FUZZ",
            "20220101000000",
        ),
        (
            "https://api.example.com/item?q=FUZZ",
            "20230101000000",
        ),
    ]
    for url, timestamp in observations:
        history.add(
            url,
            CaptureRecord(
                original=url,
                timestamp=timestamp,
                status_code="200",
                mime_type="text/html",
            ),
        )
    history.fetched = len(observations)
    return history


def test_topology_groups_by_host_and_path_with_parameter_set_variants():
    records = records_for(_topology_history(), "topology")

    assert [(record["host"], record["path"]) for record in records] == [
        ("api.example.com", "/item"),
        ("example.com", "/item"),
    ]

    item = records[1]
    assert item["schemes"] == ["http", "https"]
    assert item["captures"] == 3
    assert item["unique_urls"] == 2
    assert item["unique_parameters"] == 2
    assert item["parameters"] == ["id", "lang"]
    assert item["first_seen"] == "20200101000000"
    assert item["last_seen"] == "20220101000000"
    assert item["parameter_sets"] == [
        {
            "parameters": ["id"],
            "unique_urls": 1,
            "captures": 1,
            "first_seen": "20220101000000",
            "last_seen": "20220101000000",
        },
        {
            "parameters": ["id", "lang"],
            "unique_urls": 1,
            "captures": 2,
            "first_seen": "20200101000000",
            "last_seen": "20210101000000",
        },
    ]


def test_topology_text_output_is_deterministic():
    record = records_for(_topology_history(), "topology")[1]
    fields = format_record(record, "txt").split("\t")

    assert fields[:8] == [
        "example.com",
        "/item",
        "http,https",
        "3",
        "2",
        "2",
        "id,lang",
        "2",
    ]
    assert fields[8] == "id:1:1;id,lang:1:2"


def test_cooccurrence_counts_pairs_routes_urls_and_captures():
    history = _topology_history()
    history.add(
        "https://example.com/search?id=FUZZ&lang=FUZZ&q=FUZZ",
        CaptureRecord(
            original="https://example.com/search?id=1&lang=en&q=test",
            timestamp="20240101000000",
            status_code="200",
            mime_type="text/html",
        ),
    )

    records = {
        tuple(record["parameters"]): record for record in records_for(history, "cooccurrence")
    }

    assert records[("id", "lang")] == {
        "type": "cooccurrence",
        "domain": "example.com",
        "parameters": ["id", "lang"],
        "unique_urls": 2,
        "routes": 2,
        "captures": 3,
        "first_seen": "20200101000000",
        "last_seen": "20240101000000",
    }
    assert records[("id", "q")]["unique_urls"] == 1
    assert records[("lang", "q")]["captures"] == 1


def test_cooccurrence_ignores_single_parameter_endpoints_and_formats_text():
    records = records_for(_history(), "cooccurrence")
    assert [record["parameters"] for record in records] == [["id", "lang"]]

    fields = format_record(records[0], "txt").split("\t")
    assert fields == [
        "id",
        "lang",
        "1",
        "1",
        "2",
        "20200101000000",
        "20210101000000",
    ]


def _change_history() -> DomainHistory:
    history = DomainHistory(domain="example.com")
    captures = [
        ("https://example.com/persist?id=FUZZ", "20200101000000", "id"),
        ("https://example.com/legacy?legacy=FUZZ", "20200102000000", "legacy"),
        ("https://example.com/persist?id=FUZZ", "20210101000000", "id"),
        ("https://example.com/new?q=FUZZ", "20210102000000", "q"),
    ]
    for url, timestamp, parameter in captures:
        history.add(
            url,
            CaptureRecord(
                original=f"https://example.com/?{parameter}=1",
                timestamp=timestamp,
                status_code="200",
                mime_type="text/html",
            ),
        )
    history.fetched = len(captures)
    return history


def test_changes_report_added_removed_and_persisted_entities():
    records = records_for(
        _change_history(),
        "changes",
        compare_periods=("2020", "2021"),
    )

    summary = records[0]
    assert summary["type"] == "change_summary"
    assert summary["granularity"] == "year"
    assert summary["urls"] == {
        "baseline": 2,
        "comparison": 2,
        "added": 1,
        "removed": 1,
        "persisted": 1,
    }
    assert summary["parameters"] == {
        "baseline": 2,
        "comparison": 2,
        "added": 1,
        "removed": 1,
        "persisted": 1,
    }

    details = {(record["entity"], record["status"], record["value"]) for record in records[1:]}
    assert ("url", "persisted", "https://example.com/persist?id=FUZZ") in details
    assert ("url", "removed", "https://example.com/legacy?legacy=FUZZ") in details
    assert ("url", "added", "https://example.com/new?q=FUZZ") in details
    assert ("parameter", "persisted", "id") in details
    assert ("parameter", "removed", "legacy") in details
    assert ("parameter", "added", "q") in details


def test_changes_support_month_periods_and_deterministic_text():
    records = records_for(
        _change_history(),
        "changes",
        compare_periods=("202001", "202101"),
    )
    summary = records[0]
    assert summary["granularity"] == "month"
    assert format_record(summary, "txt").startswith("summary\t202001\t202101\t")

    detail = next(record for record in records if record.get("status") == "added")
    assert format_record(detail, "txt").split("\t")[:4] == [
        "202001",
        "202101",
        detail["entity"],
        "added",
    ]


def test_jsonl_output_is_compact_and_machine_readable():
    record = records_for(_history(), "summary")[0]
    encoded = format_record(record, "jsonl")
    assert json.loads(encoded) == record
    assert " " not in encoded


def test_text_history_output_is_tab_separated():
    record = records_for(_history(), "history")[0]
    encoded = format_record(record, "txt")
    assert "\t" in encoded
    assert encoded.count("\t") == 5


def _cfg(tmp_path, max_results=0):
    return RunConfig(
        domains=["example.com"],
        outdir=tmp_path / "out",
        write_files=False,
        analysis="history",
        max_results=max_results,
        http=HttpConfig(retries=0, backoff_base_s=0.0, max_backoff_s=0.0),
        filters=build_filter_options(),
    )


def _run(tmp_path, max_results=0):
    seen_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(dict(request.url.params))
        return httpx.Response(200, text=PAGE)

    transport = httpx.MockTransport(handler)
    real_client = analysis.httpx.AsyncClient

    def patched(**kwargs):
        kwargs.pop("proxy", None)
        kwargs.pop("proxies", None)
        return real_client(transport=transport, **kwargs)

    analysis.httpx.AsyncClient = patched  # type: ignore[assignment]
    try:
        result = asyncio.run(run_history(_cfg(tmp_path, max_results=max_results)))
    finally:
        analysis.httpx.AsyncClient = real_client  # type: ignore[assignment]
    return result, seen_requests


def test_history_runner_requests_metadata_and_disables_collapse(tmp_path):
    result, requests = _run(tmp_path)

    assert result.ok
    query = requests[0]
    assert query["fl"] == "timestamp,statuscode,mimetype,original"
    assert "collapse" not in query

    history = result.analyses["example.com"]
    assert history.fetched == 3
    assert history.accepted == 3
    assert len(history.endpoints) == 2


def test_history_max_results_caps_accepted_captures(tmp_path):
    result, _ = _run(tmp_path, max_results=1)

    assert result.ok
    (stats,) = result.stats
    assert stats.kept == 1
    assert stats.complete is False


def test_analysis_files_use_mode_specific_names(tmp_path):
    history = _history()
    cfg = RunConfig(
        domains=["example.com"],
        outdir=tmp_path,
        write_files=True,
        out_format="jsonl",
        analysis="summary",
    )
    result = HistoryRunResult(
        stats=[],
        analyses={"example.com": history},
    )

    write_analysis_files(result, cfg, "summary")

    path = tmp_path / "example.com.summary.jsonl"
    assert path.is_file()
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["type"] == "summary"
    assert record["unique_urls"] == 2


def test_history_skips_malformed_archived_urls_and_keeps_processing(tmp_path):
    page = "\n".join(
        [
            "20200101000000 200 text/html https://[example.com]/bad.php?id=1",
            "20210101000000 200 text/html https://example.com/good.php?id=2",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    transport = httpx.MockTransport(handler)
    real_client = analysis.httpx.AsyncClient

    def patched(**kwargs):
        kwargs.pop("proxy", None)
        kwargs.pop("proxies", None)
        return real_client(transport=transport, **kwargs)

    analysis.httpx.AsyncClient = patched  # type: ignore[assignment]
    try:
        result = asyncio.run(run_history(_cfg(tmp_path, max_results=1)))
    finally:
        analysis.httpx.AsyncClient = real_client  # type: ignore[assignment]

    assert result.ok
    history = result.analyses["example.com"]
    assert history.fetched == 2
    assert history.accepted == 1
    assert list(history.endpoints) == ["https://example.com/good.php?id=FUZZ"]

# SPDX-License-Identifier: GPL-3.0

"""Tests for the web UI: request translation and the access guards.

No outbound network is involved: the guards are exercised against a server
bound to an ephemeral loopback port, and no run is ever started.
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from wayparam.analysis import DomainHistory, HistoryRunResult
from wayparam.core import DomainStats, RunResult
from wayparam.gui.server import Rejected, config_from_request, is_client_gone, serve
from wayparam.output import UrlRecord
from wayparam.source_analysis import SourceSummary, SourceSummaryRunResult
from wayparam.wayback import CaptureRecord

# ---- request translation -------------------------------------------------


def test_domains_are_cleaned_and_deduped():
    cfg = config_from_request(
        {"domains": "https://Example.com/a/b\n# comment\nexample.com\n\nfoo.org, bar.org"}
    )
    assert cfg.domains == ["example.com", "foo.org", "bar.org"]


def test_domains_use_the_shared_ipv6_safe_parser():
    cfg = config_from_request({"domains": "https://[2001:DB8::1]:8443/a, https://Example.com/path"})
    assert cfg.domains == ["[2001:db8::1]:8443", "example.com"]


def test_no_domain_is_rejected():
    with pytest.raises(Rejected) as e:
        config_from_request({"domains": "  \n# only a comment\n"})
    assert e.value.status == 400


def test_defaults_match_the_cli():
    cfg = config_from_request({"domains": "example.com"})
    assert cfg.out_format == "txt"
    assert cfg.concurrency == 6
    assert cfg.normalize.placeholder == "FUZZ"
    assert cfg.normalize.only_params is True
    assert cfg.normalize.drop_tracking is True
    assert cfg.cdx.collapse == "urlkey"
    assert cfg.sources == ("wayback",)
    assert cfg.commoncrawl.indexes == ("latest",)
    assert cfg.timeline_granularity == "year"


def test_source_selection_and_commoncrawl_options():
    cfg = config_from_request(
        {
            "domains": "example.com",
            "source": "commoncrawl,wayback",
            "cc_index": "CC-MAIN-2026-39, CC-MAIN-2026-34",
            "cc_page_size": 3,
            "cc_rps": 0.5,
        }
    )
    assert cfg.sources == ("commoncrawl", "wayback")
    assert cfg.commoncrawl.indexes == ("CC-MAIN-2026-39", "CC-MAIN-2026-34")
    assert cfg.commoncrawl.page_size == 3
    assert cfg.commoncrawl.rps == 0.5


def test_invalid_source_is_rejected():
    with pytest.raises(Rejected) as exc:
        config_from_request({"domains": "example.com", "source": "unknown"})
    assert exc.value.status == 400


def test_historical_analysis_rejects_commoncrawl():
    with pytest.raises(Rejected) as exc:
        config_from_request(
            {"domains": "example.com", "source": "commoncrawl", "analysis": "history"}
        )
    assert exc.value.status == 400
    assert "Wayback only" in exc.value.message


def test_analysis_mode_is_sanitized():
    assert (
        config_from_request({"domains": "example.com", "analysis": "history"}).analysis == "history"
    )
    assert (
        config_from_request({"domains": "example.com", "analysis": "params"}).analysis == "params"
    )
    assert (
        config_from_request({"domains": "example.com", "analysis": "summary"}).analysis == "summary"
    )
    assert (
        config_from_request({"domains": "example.com", "analysis": "timeline"}).analysis
        == "timeline"
    )
    changes = config_from_request(
        {
            "domains": "example.com",
            "analysis": "changes",
            "change_baseline": "2020",
            "change_comparison": "2024",
        }
    )
    assert changes.analysis == "changes"
    assert changes.compare_periods == ("2020", "2024")
    assert (
        config_from_request({"domains": "example.com", "analysis": "topology"}).analysis
        == "topology"
    )
    assert (
        config_from_request({"domains": "example.com", "analysis": "cooccurrence"}).analysis
        == "cooccurrence"
    )
    assert config_from_request({"domains": "example.com", "analysis": "other"}).analysis is None


def test_timeline_granularity_reaches_gui_config():
    monthly = config_from_request(
        {
            "domains": "example.com",
            "analysis": "timeline",
            "timeline_granularity": "month",
        }
    )
    assert monthly.analysis == "timeline"
    assert monthly.timeline_granularity == "month"

    invalid = config_from_request(
        {
            "domains": "example.com",
            "analysis": "timeline",
            "timeline_granularity": "quarter",
        }
    )
    assert invalid.timeline_granularity == "year"


@pytest.mark.parametrize(
    ("baseline", "comparison"),
    [
        ("", "2024"),
        ("2020", ""),
        ("2020", "202401"),
        ("202413", "202501"),
        ("2024", "2020"),
    ],
)
def test_invalid_gui_change_periods_are_rejected(baseline, comparison):
    with pytest.raises(Rejected) as exc:
        config_from_request(
            {
                "domains": "example.com",
                "analysis": "changes",
                "change_baseline": baseline,
                "change_comparison": comparison,
            }
        )
    assert exc.value.status == 400
    assert "change" in exc.value.message.lower()


def test_files_are_only_written_when_an_outdir_is_given(tmp_path: Path):
    assert config_from_request({"domains": "example.com"}).write_files is False

    cfg = config_from_request({"domains": "example.com", "outdir": str(tmp_path)})
    assert cfg.write_files is True
    assert cfg.outdir == tmp_path


def test_numeric_fields_are_clamped_and_survive_garbage():
    cfg = config_from_request({"domains": "example.com", "concurrency": 9999, "limit": -5})
    assert cfg.concurrency == 64
    assert cfg.cdx.limit == 1

    cfg = config_from_request({"domains": "example.com", "concurrency": "abc"})
    assert cfg.concurrency == 6


# ---- access guards -------------------------------------------------------


@pytest.fixture
def server():
    """A server on an ephemeral loopback port.

    Package build environments (sbuild, pbuilder) may forbid sockets, so this
    skips rather than fails when the port cannot be opened.
    """
    try:
        httpd, token = serve("127.0.0.1", 0)
    except OSError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot bind a loopback socket here: {exc}")
    try:
        yield httpd.server_address[1], token
    finally:
        httpd.shutdown()


def _status(port: int, path: str, *, headers: dict | None = None, data: bytes | None = None) -> int:
    """Raw HTTP against loopback.

    Deliberately not urllib: it honours http_proxy from the environment, and a
    build environment that sets one would send these requests to a proxy that
    is not there.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST" if data else "GET", path, body=data, headers=headers or {})
        return conn.getresponse().status
    finally:
        conn.close()


def test_index_requires_the_token(server):
    port, token = server
    assert _status(port, "/") == 403
    assert _status(port, "/?t=wrong") == 403
    assert _status(port, f"/?t={token}") == 200


def test_api_requires_the_token(server):
    port, _ = server
    body = json.dumps({"domains": "example.com"}).encode()
    assert _status(port, "/api/run", data=body) == 403


def test_foreign_host_header_is_refused(server):
    """Defends against DNS rebinding: a name that resolves to 127.0.0.1."""
    port, token = server
    assert _status(port, f"/?t={token}", headers={"Host": "evil.example"}) == 421


def test_unknown_paths_are_not_found(server):
    port, token = server
    assert _status(port, f"/admin?t={token}") == 404


def test_each_process_gets_a_distinct_token():
    a, token_a = serve("127.0.0.1", 0)
    b, token_b = serve("127.0.0.1", 0)
    try:
        assert token_a != token_b
        assert len(token_a) >= 24
    finally:
        a.shutdown()
        b.shutdown()


def test_float_fields_survive_garbage():
    cfg = config_from_request({"domains": "example.com", "rps": "abc", "timeout": "soon"})
    assert cfg.rps == 0.0
    assert cfg.http.timeout_s == 30.0

    cfg = config_from_request({"domains": "example.com", "rps": 2.5, "timeout": 5})
    assert cfg.rps == 2.5
    assert cfg.http.timeout_s == 5.0


def test_float_fields_are_clamped():
    cfg = config_from_request({"domains": "example.com", "rps": -3, "timeout": 99999})
    assert cfg.rps == 0.0
    assert cfg.http.timeout_s == 600.0


def _post(port: int, token: str, body: bytes) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(
            "POST",
            "/api/run",
            body=body,
            headers={"X-Wayparam-Token": token, "Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_a_non_object_body_is_rejected_with_an_answer(server):
    """A malformed body must produce a JSON error, not a dropped connection."""
    port, token = server
    status, body = _post(port, token, b'["not", "an", "object"]')
    assert status == 400
    assert "JSON object" in json.loads(body)["error"]


# ---- the streamed run ----------------------------------------------------


def _fake_run(records, stats, errors=(), fail=None):
    """Stand in for core.run so the streaming path can be tested offline."""

    async def fake(cfg, *, on_record=None, on_progress=None):
        if fail is not None:
            raise fail
        for url in records:
            if on_record:
                on_record(UrlRecord(domain=cfg.domains[0], url=url))
        return RunResult(stats=list(stats), errors=list(errors))

    return fake


def _stream(port: int, token: str, body: dict) -> list[dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            "POST",
            "/api/run",
            body=json.dumps(body).encode(),
            headers={"X-Wayparam-Token": token, "Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        raw = resp.read().decode("utf-8")
    finally:
        conn.close()
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def test_a_run_streams_ndjson_events(server, monkeypatch):
    port, token = server
    monkeypatch.setattr(
        "wayparam.gui.server.run",
        _fake_run(
            ["https://example.com/?a=FUZZ", "https://example.com/?b=FUZZ"],
            [DomainStats(domain="example.com", fetched=9, kept=2)],
        ),
    )

    events = _stream(port, token, {"domains": "example.com"})

    assert [e["type"] for e in events] == ["start", "url", "url", "stats", "done"]
    assert events[0]["domains"] == ["example.com"]
    assert events[0]["sources"] == ["wayback"]
    assert [e["url"] for e in events if e["type"] == "url"] == [
        "https://example.com/?a=FUZZ",
        "https://example.com/?b=FUZZ",
    ]
    assert events[3] == {
        "type": "stats",
        "domain": "example.com",
        "fetched": 9,
        "kept": 2,
        "complete": True,
    }


def test_partial_stats_are_streamed_as_incomplete(server, monkeypatch):
    port, token = server
    monkeypatch.setattr(
        "wayparam.gui.server.run",
        _fake_run(
            ["https://example.com/?a=FUZZ"],
            [DomainStats(domain="example.com", fetched=4, kept=1, complete=False)],
            errors=[("example.com", RuntimeError("CDX went away"))],
        ),
    )

    events = _stream(port, token, {"domains": "example.com"})
    stats = next(e for e in events if e["type"] == "stats")
    error = next(e for e in events if e["type"] == "error")

    assert stats["complete"] is False
    assert stats["kept"] == 1
    assert "CDX went away" in error["message"]
    assert events[-1]["type"] == "done"


def test_a_failing_run_still_terminates_the_stream(server, monkeypatch):
    """An unexpected failure must reach the page, not hang the response."""
    port, token = server
    monkeypatch.setattr(
        "wayparam.gui.server.run", _fake_run([], [], fail=RuntimeError("engine exploded"))
    )

    events = _stream(port, token, {"domains": "example.com"})
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "error"
    assert "engine exploded" in events[-1]["message"]


def test_client_disconnects_are_not_treated_as_crashes():
    """A closed tab is routine traffic, not a fault to dump a traceback for."""
    assert is_client_gone(ConnectionResetError(104, "Connection reset by peer"))
    assert is_client_gone(BrokenPipeError(32, "Broken pipe"))
    assert is_client_gone(TimeoutError())
    assert not is_client_gone(RuntimeError("a real bug"))
    assert not is_client_gone(None)


def test_historical_summary_is_streamed_as_analysis_event(server, monkeypatch):
    port, token = server

    async def fake_history(cfg, *, on_progress=None):
        history = DomainHistory(domain=cfg.domains[0], fetched=1)
        history.add(
            "https://example.com/?id=FUZZ",
            CaptureRecord(
                original="https://example.com/?id=1",
                timestamp="20240102030405",
                status_code="200",
                mime_type="text/html",
            ),
        )
        return HistoryRunResult(
            stats=[DomainStats(cfg.domains[0], fetched=1, kept=1)],
            analyses={cfg.domains[0]: history},
        )

    monkeypatch.setattr("wayparam.gui.server.run_history", fake_history)
    events = _stream(
        port,
        token,
        {"domains": "example.com", "analysis": "summary", "format": "jsonl"},
    )

    assert [event["type"] for event in events] == ["start", "analysis", "stats", "done"]
    assert events[1]["mode"] == "summary"
    assert events[1]["record"]["type"] == "summary"
    assert events[1]["record"]["unique_urls"] == 1


def test_timeline_is_streamed_with_requested_granularity(server, monkeypatch):
    port, token = server

    async def fake_history(cfg, *, on_progress=None):
        history = DomainHistory(domain=cfg.domains[0], fetched=2)
        history.add(
            "https://example.com/?id=FUZZ",
            CaptureRecord(
                original="https://example.com/?id=1",
                timestamp="20240102030405",
                status_code="200",
                mime_type="text/html",
            ),
        )
        history.add(
            "https://example.com/search?q=FUZZ",
            CaptureRecord(
                original="https://example.com/search?q=test",
                timestamp="20240203040506",
                status_code="200",
                mime_type="text/html",
            ),
        )
        return HistoryRunResult(
            stats=[DomainStats(cfg.domains[0], fetched=2, kept=2)],
            analyses={cfg.domains[0]: history},
        )

    monkeypatch.setattr("wayparam.gui.server.run_history", fake_history)
    events = _stream(
        port,
        token,
        {
            "domains": "example.com",
            "analysis": "timeline",
            "timeline_granularity": "month",
            "format": "jsonl",
        },
    )

    analyses = [event for event in events if event["type"] == "analysis"]
    assert events[0]["timeline_granularity"] == "month"
    assert [event["mode"] for event in analyses] == ["timeline", "timeline"]
    assert [event["record"]["period"] for event in analyses] == ["202401", "202402"]
    assert analyses[0]["record"]["new_urls"] == 1
    assert analyses[1]["record"]["new_parameters"] == 1


def test_temporal_changes_are_streamed_from_gui(server, monkeypatch):
    port, token = server

    async def fake_history(cfg, *, on_progress=None):
        history = DomainHistory(domain=cfg.domains[0], fetched=3)
        history.add(
            "https://example.com/persist?id=FUZZ",
            CaptureRecord(
                original="https://example.com/persist?id=1",
                timestamp="20200102030405",
                status_code="200",
                mime_type="text/html",
            ),
        )
        history.add(
            "https://example.com/persist?id=FUZZ",
            CaptureRecord(
                original="https://example.com/persist?id=2",
                timestamp="20240102030405",
                status_code="200",
                mime_type="text/html",
            ),
        )
        history.add(
            "https://example.com/new?q=FUZZ",
            CaptureRecord(
                original="https://example.com/new?q=test",
                timestamp="20240203040506",
                status_code="200",
                mime_type="text/html",
            ),
        )
        return HistoryRunResult(
            stats=[DomainStats(cfg.domains[0], fetched=3, kept=3)],
            analyses={cfg.domains[0]: history},
        )

    monkeypatch.setattr("wayparam.gui.server.run_history", fake_history)
    events = _stream(
        port,
        token,
        {
            "domains": "example.com",
            "analysis": "changes",
            "change_baseline": "2020",
            "change_comparison": "2024",
            "format": "jsonl",
        },
    )

    analyses = [event for event in events if event["type"] == "analysis"]
    assert events[0]["compare_periods"] == ["2020", "2024"]
    assert analyses[0]["mode"] == "changes"
    assert analyses[0]["record"]["type"] == "change_summary"
    assert analyses[0]["record"]["urls"]["added"] == 1
    assert any(
        event["record"].get("status") == "persisted"
        and event["record"].get("value") == "https://example.com/persist?id=FUZZ"
        for event in analyses[1:]
    )


@pytest.mark.parametrize(
    ("mode", "expected_type"),
    [
        ("topology", "topology"),
        ("cooccurrence", "cooccurrence"),
    ],
)
def test_surface_intelligence_views_are_streamed_from_gui(
    server, monkeypatch, mode, expected_type
):
    port, token = server

    async def fake_history(cfg, *, on_progress=None):
        history = DomainHistory(domain=cfg.domains[0], fetched=2)
        history.add(
            "https://example.com/item?id=FUZZ&lang=FUZZ",
            CaptureRecord(
                original="https://example.com/item?id=1&lang=en",
                timestamp="20200102030405",
                status_code="200",
                mime_type="text/html",
            ),
        )
        history.add(
            "https://example.com/item?id=FUZZ&q=FUZZ",
            CaptureRecord(
                original="https://example.com/item?id=2&q=test",
                timestamp="20240102030405",
                status_code="200",
                mime_type="text/html",
            ),
        )
        return HistoryRunResult(
            stats=[DomainStats(cfg.domains[0], fetched=2, kept=2)],
            analyses={cfg.domains[0]: history},
        )

    monkeypatch.setattr("wayparam.gui.server.run_history", fake_history)
    events = _stream(
        port,
        token,
        {
            "domains": "example.com",
            "analysis": mode,
            "format": "jsonl",
        },
    )

    analyses = [event for event in events if event["type"] == "analysis"]
    assert events[0]["analysis"] == mode
    assert analyses
    assert all(event["mode"] == mode for event in analyses)
    assert analyses[0]["record"]["type"] == expected_type

    if mode == "topology":
        assert analyses[0]["record"]["host"] == "example.com"
        assert analyses[0]["record"]["path"] == "/item"
        assert analyses[0]["record"]["parameters"] == ["id", "lang", "q"]
    else:
        pairs = {tuple(event["record"]["parameters"]) for event in analyses}
        assert pairs == {("id", "lang"), ("id", "q")}


def test_gui_provenance_reaches_config():
    cfg = config_from_request(
        {
            "domains": "example.com",
            "source": "wayback,commoncrawl",
            "format": "jsonl",
            "provenance": True,
        }
    )
    assert cfg.provenance is True
    assert cfg.source_summary is False


def test_gui_provenance_requires_jsonl():
    with pytest.raises(Rejected) as exc:
        config_from_request(
            {
                "domains": "example.com",
                "source": "wayback,commoncrawl",
                "format": "txt",
                "provenance": True,
            }
        )
    assert exc.value.status == 400
    assert "JSONL" in exc.value.message


def test_gui_source_summary_requires_multiple_sources():
    with pytest.raises(Rejected) as exc:
        config_from_request(
            {
                "domains": "example.com",
                "source": "wayback",
                "source_summary": True,
            }
        )
    assert exc.value.status == 400
    assert "at least two" in exc.value.message


def test_gui_source_summary_reaches_config():
    cfg = config_from_request(
        {
            "domains": "example.com",
            "source": "wayback,commoncrawl",
            "source_summary": True,
        }
    )
    assert cfg.source_summary is True
    assert cfg.provenance is False


def test_gui_rejects_provenance_with_source_summary():
    with pytest.raises(Rejected) as exc:
        config_from_request(
            {
                "domains": "example.com",
                "source": "wayback,commoncrawl",
                "format": "jsonl",
                "provenance": True,
                "source_summary": True,
            }
        )
    assert exc.value.status == 400
    assert "mutually exclusive" in exc.value.message


def test_source_summary_is_streamed_as_dedicated_event(server, monkeypatch):
    port, token = server

    async def fake_source_summary(cfg, *, on_progress=None):
        summary = SourceSummary(
            domain=cfg.domains[0],
            sources=("wayback", "commoncrawl"),
            union_urls=3,
            evidence_records=4,
            source_counts={"wayback": 2, "commoncrawl": 2},
            exclusive_counts={"wayback": 1, "commoncrawl": 1},
            shared_urls=1,
            overlap_urls=1,
            complete=True,
        )
        return SourceSummaryRunResult(
            stats=[DomainStats(cfg.domains[0], fetched=4, kept=4)],
            summaries={cfg.domains[0]: summary},
        )

    monkeypatch.setattr("wayparam.gui.server.run_source_summary", fake_source_summary)
    events = _stream(
        port,
        token,
        {
            "domains": "example.com",
            "source": "wayback,commoncrawl",
            "source_summary": True,
            "format": "jsonl",
        },
    )

    assert [event["type"] for event in events] == [
        "start",
        "source_summary",
        "stats",
        "done",
    ]
    assert events[0]["source_summary"] is True
    assert events[1]["record"]["type"] == "source_summary"
    assert events[1]["record"]["union_urls"] == 3
    assert events[1]["record"]["overlap_urls"] == 1


def test_provenance_stream_exposes_source_and_timestamp(server, monkeypatch):
    port, token = server

    async def fake(cfg, *, on_record=None, on_progress=None):
        if on_record:
            on_record(
                UrlRecord(
                    domain=cfg.domains[0],
                    url="https://example.com/?id=FUZZ",
                    source="commoncrawl",
                    fetched_at="2026-09-21T12:34:56+00:00",
                )
            )
        return RunResult(stats=[DomainStats(cfg.domains[0], fetched=1, kept=1)])

    monkeypatch.setattr("wayparam.gui.server.run", fake)
    events = _stream(
        port,
        token,
        {
            "domains": "example.com",
            "source": "wayback,commoncrawl",
            "provenance": True,
            "format": "jsonl",
        },
    )

    url_event = next(event for event in events if event["type"] == "url")
    assert url_event["source"] == "commoncrawl"
    assert url_event["fetched_at"] == "2026-09-21T12:34:56+00:00"
    assert events[0]["provenance"] is True

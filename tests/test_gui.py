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

from wayparam.core import DomainStats, RunResult
from wayparam.gui.server import Rejected, config_from_request, is_client_gone, serve
from wayparam.output import UrlRecord

# ---- request translation -------------------------------------------------


def test_domains_are_cleaned_and_deduped():
    cfg = config_from_request(
        {"domains": "https://Example.com/a/b\n# comment\nexample.com\n\nfoo.org, bar.org"}
    )
    assert cfg.domains == ["example.com", "foo.org", "bar.org"]


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

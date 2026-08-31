# SPDX-License-Identifier: GPL-3.0

"""Tests for the web UI: request translation and the access guards.

No outbound network is involved: the guards are exercised against a server
bound to an ephemeral loopback port, and no run is ever started.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wayparam.gui.server import Rejected, config_from_request, serve

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
    httpd, token = serve("127.0.0.1", 0)
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}", token
    httpd.shutdown()


def _status(url: str, *, headers: dict | None = None, data: bytes | None = None) -> int:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_index_requires_the_token(server):
    base, token = server
    assert _status(f"{base}/") == 403
    assert _status(f"{base}/?t=wrong") == 403
    assert _status(f"{base}/?t={token}") == 200


def test_api_requires_the_token(server):
    base, _ = server
    body = json.dumps({"domains": "example.com"}).encode()
    assert _status(f"{base}/api/run", data=body) == 403


def test_foreign_host_header_is_refused(server):
    """Defends against DNS rebinding: a name that resolves to 127.0.0.1."""
    base, token = server
    assert _status(f"{base}/?t={token}", headers={"Host": "evil.example"}) == 421


def test_unknown_paths_are_not_found(server):
    base, token = server
    assert _status(f"{base}/admin?t={token}") == 404


def test_each_process_gets_a_distinct_token():
    a, token_a = serve("127.0.0.1", 0)
    b, token_b = serve("127.0.0.1", 0)
    try:
        assert token_a != token_b
        assert len(token_a) >= 24
    finally:
        a.shutdown()
        b.shutdown()

# SPDX-License-Identifier: GPL-3.0

"""Tests for the `wayparam-gui` entry point that do not start a server."""

from __future__ import annotations

import pytest

from wayparam import __version__
from wayparam.gui import build_arg_parser, main


def test_defaults_are_loopback_only():
    args = build_arg_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.no_browser is False


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_binding_beyond_loopback_is_refused(host, capsys):
    """The UI makes outbound requests for whoever reaches it, so it stays local."""
    assert main(["--host", host]) == 2
    err = capsys.readouterr().err
    assert "loopback-only" in err
    assert "SSH port forwarding" in err


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_names_are_accepted(host, monkeypatch):
    """Accepted hosts must get past the guard and reach serve()."""
    called = {}

    def fake_serve(h, p):
        called["host"] = h
        raise OSError("refusing to actually listen in a test")

    monkeypatch.setattr("wayparam.gui.serve", fake_serve)
    assert main(["--host", host, "--no-browser"]) == 1  # serve() failed, not the guard
    assert called["host"] == host


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        build_arg_parser().parse_args(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out

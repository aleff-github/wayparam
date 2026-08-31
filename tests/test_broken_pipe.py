# SPDX-License-Identifier: GPL-3.0

"""Regression tests for BrokenPipeError handling.

`wayparam --stdout | head` is a primary use case: when the consumer goes away
the tool must exit quietly instead of printing a traceback.
"""

from __future__ import annotations

import os

from wayparam import cli

ARGV = ["-d", "example.com", "--stdout", "--no-files"]


def test_main_returns_141_on_broken_pipe(monkeypatch, capfd):
    async def boom(_cfg, **_kw):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli, "run", boom)

    # main() points stdout's descriptor at /dev/null so the interpreter's
    # flush-on-exit stays quiet. Step outside pytest's fd capture and restore
    # the descriptor afterwards, otherwise the capture teardown is left with a
    # dangling fd.
    with capfd.disabled():
        saved = os.dup(1)
        try:
            assert cli.main(ARGV) == 141
        finally:
            os.dup2(saved, 1)
            os.close(saved)


def test_main_still_returns_130_on_interrupt(monkeypatch):
    async def boom(_cfg, **_kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run", boom)
    assert cli.main(ARGV) == 130

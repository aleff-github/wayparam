# SPDX-License-Identifier: GPL-3.0

"""Regression tests for BrokenPipeError handling.

`wayparam --stdout | head` is a primary use case: when the consumer goes away
the tool must exit quietly instead of printing a traceback.
"""

from __future__ import annotations

import os

import pytest

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


def test_invalid_exclude_path_regex_is_a_usage_error(capsys):
    """A bad regex must exit 2 with one line, not unwind a traceback."""
    with pytest.raises(SystemExit) as e:
        cli.main(["-d", "example.com", "--stdout", "--no-files", "--exclude-path-regex", "["])
    assert e.value.code == 2
    assert "invalid --exclude-path-regex" in capsys.readouterr().err


def test_unreadable_domain_list_is_a_usage_error(capsys, tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["-l", str(tmp_path / "nope.txt"), "--stdout", "--no-files"])
    assert e.value.code == 2
    assert "cannot read the domain list" in capsys.readouterr().err


def test_an_empty_domain_list_is_a_usage_error(capsys, tmp_path):
    empty = tmp_path / "domains.txt"
    empty.write_text("# only a comment\n\n")
    with pytest.raises(SystemExit) as e:
        cli.main(["-l", str(empty), "--stdout", "--no-files"])
    assert e.value.code == 2
    assert "no domains to process" in capsys.readouterr().err


def test_pagination_settings_reach_the_config():
    parser = cli.build_arg_parser()
    args = parser.parse_args(
        [
            "-d",
            "example.com",
            "--max-results",
            "25",
            "--limit",
            "500",
            "--pagination",
            "resume",
            "--block-size",
            "20",
        ]
    )
    cfg = cli.build_config(args)
    assert cfg.max_results == 25
    assert cfg.cdx.limit == 500
    assert cfg.cdx.pagination == "resume"
    assert cfg.cdx.block_size == 20


def test_pagination_defaults_to_the_lossless_mode():
    parser = cli.build_arg_parser()
    cfg = cli.build_config(parser.parse_args(["-d", "example.com"]))
    assert cfg.cdx.pagination == "auto"
    assert cfg.cdx.block_size == 100


def test_an_unusable_block_size_is_clamped():
    parser = cli.build_arg_parser()
    cfg = cli.build_config(parser.parse_args(["-d", "example.com", "--block-size", "0"]))
    assert cfg.cdx.block_size == 1


def test_a_negative_max_results_means_unlimited():
    parser = cli.build_arg_parser()
    cfg = cli.build_config(parser.parse_args(["-d", "example.com", "--max-results", "-5"]))
    assert cfg.max_results == 0


def test_exit_code_is_2_when_a_domain_failed_even_with_partial_stats(monkeypatch, capsys):
    """Partial stats must not make a failed run look successful."""
    from wayparam.core import DomainStats, RunResult

    async def partial(_cfg, **_kw):
        return RunResult(
            stats=[DomainStats(domain="example.com", fetched=7, kept=3, complete=False)],
            errors=[("example.com", RuntimeError("CDX went away"))],
        )

    monkeypatch.setattr(cli, "run", partial)
    assert cli.main(ARGV + ["--stats"]) == 2
    err = capsys.readouterr().err
    assert "fetched=7 kept=3 (incomplete)" in err


def test_exit_code_is_0_when_a_budget_stopped_the_run(monkeypatch):
    """Hitting --max-results is a clean stop, not a failure."""
    from wayparam.core import DomainStats, RunResult

    async def capped(_cfg, **_kw):
        return RunResult(stats=[DomainStats("example.com", 90, 10, complete=False)])

    monkeypatch.setattr(cli, "run", capped)
    assert cli.main(ARGV + ["--max-results", "10"]) == 0

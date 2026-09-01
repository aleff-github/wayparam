# SPDX-License-Identifier: GPL-3.0

"""Tests for domain-list parsing (`-l`), including the '-' stdin form."""

from __future__ import annotations

import io

import pytest

from wayparam.io import ensure_dir, read_domains

LIST = """
# a comment
https://Example.com/a/b?q=1
example.com
EXAMPLE.com

http://sub.example.org:8080/path
plain.test/some/path
   spaced.test
"""


def test_read_domains_normalizes_and_dedupes(tmp_path):
    path = tmp_path / "domains.txt"
    path.write_text(LIST, encoding="utf-8")

    assert read_domains(str(path)) == [
        "example.com",
        "sub.example.org:8080",
        "plain.test",
        "spaced.test",
    ]


def test_read_domains_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("a.test\n# skip\nb.test\na.test\n"))
    assert read_domains("-") == ["a.test", "b.test"]


def test_read_domains_on_an_empty_file(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("# nothing here\n\n", encoding="utf-8")
    assert read_domains(str(path)) == []


def test_read_domains_tolerates_undecodable_bytes(tmp_path):
    path = tmp_path / "domains.txt"
    path.write_bytes(b"good.test\n\xff\xfe\nother.test\n")
    assert "good.test" in read_domains(str(path))


def test_read_domains_propagates_a_missing_file(tmp_path):
    """The CLI turns this into a usage error; the reader itself must not hide it."""
    with pytest.raises(OSError):
        read_domains(str(tmp_path / "nope.txt"))


def test_ensure_dir_is_idempotent(tmp_path):
    target = tmp_path / "a" / "b"
    ensure_dir(target)
    ensure_dir(target)
    assert target.is_dir()

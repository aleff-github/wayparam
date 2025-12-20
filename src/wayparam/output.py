# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TextIO

OutputFormat = Literal["txt", "jsonl"]


@dataclass(frozen=True)
class UrlRecord:
    domain: str
    url: str
    source: str = "wayback"
    fetched_at: str | None = None


def now_utc_iso() -> str:
    """UTC timestamp suitable for logs/JSONL (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_outfile(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8", newline="\n")


def write_record(fh: TextIO, rec: UrlRecord, fmt: OutputFormat) -> None:
    if fmt == "txt":
        fh.write(rec.url + "\n")
        return
    fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")


def print_record_stdout(rec: UrlRecord, fmt: OutputFormat) -> None:
    if fmt == "txt":
        print(rec.url, flush=True)
        return
    print(json.dumps(asdict(rec), ensure_ascii=False), flush=True)


def print_hint_stderr(message: str) -> None:
    """Diagnostics that must not pollute stdout/pipes."""
    print(message, file=sys.stderr, flush=True)

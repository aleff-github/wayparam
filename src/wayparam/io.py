# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

from pathlib import Path


def read_domains(path: str) -> list[str]:
    """
    Read domains from a file (or '-' for stdin). Normalizes:
    - strips scheme
    - strips paths
    - lowercases
    - drops blank/comment lines
    """
    import sys
    from urllib.parse import urlsplit

    if path == "-":
        content = sys.stdin.read().splitlines()
    else:
        content = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()

    out: list[str] = []
    for line in content:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" in line:
            parts = urlsplit(line)
            host = parts.netloc
        else:
            host = line.split("/")[0]
        host = host.strip().lower()
        if host:
            out.append(host)

    seen = set()
    deduped = []
    for d in out:
        if d not in seen:
            seen.add(d)
            deduped.append(d)
    return deduped


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

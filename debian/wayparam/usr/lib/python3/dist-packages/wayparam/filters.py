# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

DEFAULT_EXT_BLACKLIST = {
    ".7z",
    ".avi",
    ".bmp",
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".eot",
    ".eps",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".iso",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".otf",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rss",
    ".svg",
    ".tar",
    ".tif",
    ".tiff",
    ".ttf",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
    ".zip",
}


@dataclass(frozen=True)
class FilterOptions:
    ext_blacklist: set[str]
    ext_whitelist: set[str] | None = None
    path_exclude_regex: list[re.Pattern] | None = None


def _path_extension(url: str) -> str:
    try:
        path = urlsplit(url).path
    except Exception:
        return ""
    return PurePosixPath(path).suffix.lower()


def is_boring(url: str, opt: FilterOptions) -> bool:
    ext = _path_extension(url)

    if opt.ext_whitelist is not None:
        if ext and ext not in opt.ext_whitelist:
            return True

    if ext and ext in opt.ext_blacklist:
        return True

    if opt.path_exclude_regex:
        path = urlsplit(url).path
        for rx in opt.path_exclude_regex:
            if rx.search(path):
                return True

    return False


def parse_ext_set(csv: str) -> set[str]:
    """
    Parse comma-separated extensions like ".png,.jpg,css" -> {".png",".jpg",".css"}
    """
    out: set[str] = set()
    for raw in csv.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if not raw.startswith("."):
            raw = "." + raw
        out.add(raw.lower())
    return out

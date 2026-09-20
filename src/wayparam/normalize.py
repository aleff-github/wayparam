# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {
    "gclid",
    "fbclid",
    "msclkid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "yclid",
    "gbraid",
    "wbraid",
    "twclid",
}


@dataclass(frozen=True)
class NormalizeOptions:
    placeholder: str = "FUZZ"
    keep_values: bool = False
    only_params: bool = True
    drop_tracking: bool = True
    drop_empty: bool = True
    sort_params: bool = True


def _is_tracking_key(k: str) -> bool:
    k_l = k.lower()
    if k_l in TRACKING_KEYS:
        return True
    return any(k_l.startswith(p) for p in TRACKING_PREFIXES)


_DEFAULT_PORTS = {("http", 80), ("https", 443)}


def canonicalize_url(url: str, opt: NormalizeOptions) -> str | None:
    """
    Returns a canonicalized URL or None if filtered out by only_params / drop_empty.

    - removes fragments
    - normalizes default ports
    - sorts params
    - optionally drops tracking params
    - optionally masks values
    """
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None

    if not parts.scheme or not parts.netloc or not host:
        return None

    scheme = parts.scheme.lower()

    # urllib.parse already separates bracketed IPv6 literals from ports.
    # Splitting netloc on ":" breaks because ":" is part of an IPv6 host.
    host = host.lower()
    hostport_norm = f"[{host}]" if ":" in host else host
    if port is not None and (scheme, port) not in _DEFAULT_PORTS:
        hostport_norm += f":{port}"

    userinfo = parts.netloc.rsplit("@", 1)[0] if "@" in parts.netloc else ""
    netloc_norm = (userinfo + "@" if userinfo else "") + hostport_norm

    path = parts.path or "/"

    qsl = parse_qsl(parts.query, keep_blank_values=True)
    out: list[tuple[str, str]] = []
    for k, v in qsl:
        if opt.drop_tracking and _is_tracking_key(k):
            continue
        if opt.drop_empty and k.strip() == "":
            continue
        out.append((k, v if opt.keep_values else opt.placeholder))

    # Masking collapses distinct values, so `?id=1&id=2` would otherwise come
    # out as `?id=FUZZ&id=FUZZ`. Identical pairs add nothing either way.
    seen_pairs: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for kv in out:
        if kv in seen_pairs:
            continue
        seen_pairs.add(kv)
        unique.append(kv)
    out = unique

    if opt.sort_params:
        out.sort(key=lambda kv: (kv[0].lower(), kv[0], kv[1]))

    query = urlencode(out, doseq=True)

    if opt.only_params and not query:
        return None

    return urlunsplit((scheme, netloc_norm, path, query, ""))

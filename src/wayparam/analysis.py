# SPDX-License-Identifier: GPL-3.0

"""Historical aggregation over Wayback CDX capture metadata."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from itertools import combinations
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

from .config import AnalysisMode, RunConfig, TimelineGranularity
from .core import Budget, DomainStats, ProgressCallback, client_kwargs
from .filters import is_boring
from .normalize import canonicalize_url
from .output import OutputFormat, open_outfile
from .ratelimit import RateLimiter
from .wayback import CaptureRecord, iter_captures

log = logging.getLogger("wayparam")

_PROGRESS_EVERY = 1000


def _min_ts(current: str | None, candidate: str | None) -> str | None:
    if not candidate:
        return current
    if not current:
        return candidate
    return min(current, candidate)


def _max_ts(current: str | None, candidate: str | None) -> str | None:
    if not candidate:
        return current
    if not current:
        return candidate
    return max(current, candidate)


@dataclass
class EndpointHistory:
    url: str
    captures: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    status_codes: Counter[str] = field(default_factory=Counter)
    mime_types: Counter[str] = field(default_factory=Counter)
    capture_months: set[str] = field(default_factory=set)

    def add(self, capture: CaptureRecord) -> None:
        self.captures += 1
        self.first_seen = _min_ts(self.first_seen, capture.timestamp)
        self.last_seen = _max_ts(self.last_seen, capture.timestamp)
        if capture.status_code:
            self.status_codes[capture.status_code] += 1
        if capture.mime_type:
            self.mime_types[capture.mime_type] += 1
        if capture.timestamp and len(capture.timestamp) >= 6:
            self.capture_months.add(capture.timestamp[:6])


@dataclass
class ParameterHistory:
    parameter: str
    endpoints: int = 0
    captures: int = 0
    first_seen: str | None = None
    last_seen: str | None = None

    def add_endpoint(self, endpoint: EndpointHistory) -> None:
        self.endpoints += 1
        self.captures += endpoint.captures
        self.first_seen = _min_ts(self.first_seen, endpoint.first_seen)
        self.last_seen = _max_ts(self.last_seen, endpoint.last_seen)


@dataclass
class ParameterSetHistory:
    parameters: tuple[str, ...]
    urls: int = 0
    captures: int = 0
    first_seen: str | None = None
    last_seen: str | None = None

    def add_endpoint(self, endpoint: EndpointHistory) -> None:
        self.urls += 1
        self.captures += endpoint.captures
        self.first_seen = _min_ts(self.first_seen, endpoint.first_seen)
        self.last_seen = _max_ts(self.last_seen, endpoint.last_seen)


@dataclass
class PathTopology:
    host: str
    path: str
    schemes: set[str] = field(default_factory=set)
    urls: int = 0
    captures: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    parameters: set[str] = field(default_factory=set)
    parameter_sets: dict[tuple[str, ...], ParameterSetHistory] = field(default_factory=dict)

    def add_endpoint(self, endpoint: EndpointHistory) -> None:
        parts = urlsplit(endpoint.url)
        if parts.scheme:
            self.schemes.add(parts.scheme)
        self.urls += 1
        self.captures += endpoint.captures
        self.first_seen = _min_ts(self.first_seen, endpoint.first_seen)
        self.last_seen = _max_ts(self.last_seen, endpoint.last_seen)

        names = tuple(sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)}))
        self.parameters.update(names)
        variant = self.parameter_sets.get(names)
        if variant is None:
            variant = ParameterSetHistory(parameters=names)
            self.parameter_sets[names] = variant
        variant.add_endpoint(endpoint)


@dataclass
class ParameterPairHistory:
    left: str
    right: str
    urls: int = 0
    captures: int = 0
    routes: set[tuple[str, str]] = field(default_factory=set)
    first_seen: str | None = None
    last_seen: str | None = None

    def add_endpoint(self, endpoint: EndpointHistory) -> None:
        parts = urlsplit(endpoint.url)
        self.urls += 1
        self.captures += endpoint.captures
        self.routes.add((parts.netloc, parts.path or "/"))
        self.first_seen = _min_ts(self.first_seen, endpoint.first_seen)
        self.last_seen = _max_ts(self.last_seen, endpoint.last_seen)


@dataclass
class DomainHistory:
    domain: str
    fetched: int = 0
    accepted: int = 0
    endpoints: dict[str, EndpointHistory] = field(default_factory=dict)
    accepted_captures_by_month: Counter[str] = field(default_factory=Counter)

    def add(self, canonical_url: str, capture: CaptureRecord) -> None:
        self.accepted += 1
        endpoint = self.endpoints.get(canonical_url)
        if endpoint is None:
            endpoint = EndpointHistory(url=canonical_url)
            self.endpoints[canonical_url] = endpoint
        endpoint.add(capture)
        if capture.timestamp and len(capture.timestamp) >= 6:
            self.accepted_captures_by_month[capture.timestamp[:6]] += 1

    def parameters(self) -> dict[str, ParameterHistory]:
        params: dict[str, ParameterHistory] = {}
        for endpoint in self.endpoints.values():
            names = {
                key for key, _ in parse_qsl(urlsplit(endpoint.url).query, keep_blank_values=True)
            }
            for name in names:
                item = params.get(name)
                if item is None:
                    item = ParameterHistory(parameter=name)
                    params[name] = item
                item.add_endpoint(endpoint)
        return params

    def cooccurrence(self) -> list[dict]:
        """Aggregate unordered parameter pairs observed on the same archived endpoint."""
        pairs: dict[tuple[str, str], ParameterPairHistory] = {}

        for endpoint in self.endpoints.values():
            names = sorted(
                {key for key, _ in parse_qsl(urlsplit(endpoint.url).query, keep_blank_values=True)}
            )
            for left, right in combinations(names, 2):
                key = (left, right)
                item = pairs.get(key)
                if item is None:
                    item = ParameterPairHistory(left=left, right=right)
                    pairs[key] = item
                item.add_endpoint(endpoint)

        return [
            {
                "type": "cooccurrence",
                "domain": self.domain,
                "parameters": [item.left, item.right],
                "unique_urls": item.urls,
                "routes": len(item.routes),
                "captures": item.captures,
                "first_seen": item.first_seen,
                "last_seen": item.last_seen,
            }
            for _, item in sorted(pairs.items())
        ]

    def topology(self) -> list[dict]:
        """Group archived endpoint variants by host/path structure."""
        groups: dict[tuple[str, str], PathTopology] = {}

        for endpoint in self.endpoints.values():
            parts = urlsplit(endpoint.url)
            host = parts.netloc
            path = parts.path or "/"
            key = (host, path)
            item = groups.get(key)
            if item is None:
                item = PathTopology(host=host, path=path)
                groups[key] = item
            item.add_endpoint(endpoint)

        records: list[dict] = []
        for key in sorted(groups):
            item = groups[key]
            variants = [
                {
                    "parameters": list(variant.parameters),
                    "unique_urls": variant.urls,
                    "captures": variant.captures,
                    "first_seen": variant.first_seen,
                    "last_seen": variant.last_seen,
                }
                for _, variant in sorted(item.parameter_sets.items())
            ]
            records.append(
                {
                    "type": "topology",
                    "domain": self.domain,
                    "host": item.host,
                    "path": item.path,
                    "schemes": sorted(item.schemes),
                    "captures": item.captures,
                    "unique_urls": item.urls,
                    "unique_parameters": len(item.parameters),
                    "parameters": sorted(item.parameters),
                    "parameter_sets": variants,
                    "first_seen": item.first_seen,
                    "last_seen": item.last_seen,
                }
            )
        return records

    def first_seen(self) -> str | None:
        value: str | None = None
        for endpoint in self.endpoints.values():
            value = _min_ts(value, endpoint.first_seen)
        return value

    def last_seen(self) -> str | None:
        value: str | None = None
        for endpoint in self.endpoints.values():
            value = _max_ts(value, endpoint.last_seen)
        return value

    def status_codes(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for endpoint in self.endpoints.values():
            total.update(endpoint.status_codes)
        return total

    def mime_types(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for endpoint in self.endpoints.values():
            total.update(endpoint.mime_types)
        return total

    def _entities_for_period(self, period: str) -> tuple[set[str], set[str]]:
        urls: set[str] = set()
        parameters: set[str] = set()
        for endpoint in self.endpoints.values():
            if not any(month.startswith(period) for month in endpoint.capture_months):
                continue
            urls.add(endpoint.url)
            parameters.update(
                key for key, _ in parse_qsl(urlsplit(endpoint.url).query, keep_blank_values=True)
            )
        return urls, parameters

    def changes(self, baseline: str, comparison: str) -> list[dict]:
        """Compare normalized URL and parameter evidence between two periods."""
        baseline_urls, baseline_parameters = self._entities_for_period(baseline)
        comparison_urls, comparison_parameters = self._entities_for_period(comparison)

        url_added = comparison_urls - baseline_urls
        url_removed = baseline_urls - comparison_urls
        url_persisted = baseline_urls & comparison_urls
        parameter_added = comparison_parameters - baseline_parameters
        parameter_removed = baseline_parameters - comparison_parameters
        parameter_persisted = baseline_parameters & comparison_parameters

        summary = {
            "type": "change_summary",
            "domain": self.domain,
            "baseline": baseline,
            "comparison": comparison,
            "granularity": "year" if len(baseline) == 4 else "month",
            "urls": {
                "baseline": len(baseline_urls),
                "comparison": len(comparison_urls),
                "added": len(url_added),
                "removed": len(url_removed),
                "persisted": len(url_persisted),
            },
            "parameters": {
                "baseline": len(baseline_parameters),
                "comparison": len(comparison_parameters),
                "added": len(parameter_added),
                "removed": len(parameter_removed),
                "persisted": len(parameter_persisted),
            },
        }

        records = [summary]
        groups = (
            ("url", "added", url_added),
            ("url", "removed", url_removed),
            ("url", "persisted", url_persisted),
            ("parameter", "added", parameter_added),
            ("parameter", "removed", parameter_removed),
            ("parameter", "persisted", parameter_persisted),
        )
        for entity, status, values in groups:
            records.extend(
                {
                    "type": "change",
                    "domain": self.domain,
                    "baseline": baseline,
                    "comparison": comparison,
                    "entity": entity,
                    "status": status,
                    "value": value,
                }
                for value in sorted(values)
            )
        return records

    def timeline(self, granularity: TimelineGranularity = "year") -> list[dict]:
        """Aggregate accepted archive evidence into deterministic time buckets."""

        def period(month: str) -> str:
            return month[:4] if granularity == "year" else month

        captures: Counter[str] = Counter()
        for month, count in self.accepted_captures_by_month.items():
            captures[period(month)] += count

        urls: Counter[str] = Counter()
        new_urls: Counter[str] = Counter()
        params_by_period: dict[str, set[str]] = {}

        for endpoint in self.endpoints.values():
            endpoint_periods = {period(month) for month in endpoint.capture_months}
            names = {
                key for key, _ in parse_qsl(urlsplit(endpoint.url).query, keep_blank_values=True)
            }
            for bucket in endpoint_periods:
                urls[bucket] += 1
                params_by_period.setdefault(bucket, set()).update(names)

            if endpoint.first_seen and len(endpoint.first_seen) >= 6:
                new_urls[period(endpoint.first_seen[:6])] += 1

        new_params: Counter[str] = Counter()
        for item in self.parameters().values():
            if item.first_seen and len(item.first_seen) >= 6:
                new_params[period(item.first_seen[:6])] += 1

        periods = sorted(
            set(captures) | set(urls) | set(new_urls) | set(params_by_period) | set(new_params)
        )
        return [
            {
                "type": "timeline",
                "domain": self.domain,
                "period": bucket,
                "captures": captures[bucket],
                "unique_urls": urls[bucket],
                "new_urls": new_urls[bucket],
                "unique_parameters": len(params_by_period.get(bucket, set())),
                "new_parameters": new_params[bucket],
            }
            for bucket in periods
        ]


@dataclass
class HistoryRunResult:
    stats: list[DomainStats] = field(default_factory=list)
    analyses: dict[str, DomainHistory] = field(default_factory=dict)
    errors: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


async def _process_domain(
    domain: str,
    cfg: RunConfig,
    *,
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter | None,
    on_progress: ProgressCallback | None,
    budget: Budget,
) -> tuple[DomainStats, DomainHistory, Exception | None]:
    history = DomainHistory(domain=domain)
    complete = True
    error: Exception | None = None

    # Historical statistics require every capture. collapse=urlkey would keep
    # only one row per URL and make first/last seen and capture counts wrong.
    cdx = replace(cfg.cdx, collapse=None, metadata=True)
    captures = iter_captures(
        domain,
        client=client,
        http_config=cfg.http,
        rate_limiter=rate_limiter,
        opt=cdx,
    )

    try:
        async for capture in captures:
            history.fetched += 1
            if on_progress and history.fetched % _PROGRESS_EVERY == 0:
                on_progress(domain, history.fetched, history.accepted)

            if is_boring(capture.original, cfg.filters):
                continue

            canonical = canonicalize_url(capture.original, cfg.normalize)
            if canonical is None:
                continue

            if not budget.take():
                complete = False
                break

            history.add(canonical, capture)
    except BrokenPipeError:
        raise
    except Exception as exc:  # noqa: BLE001
        complete = False
        error = exc
    finally:
        try:
            await captures.aclose()
        except Exception:  # noqa: BLE001
            log.debug("closing the historical CDX iterator failed", exc_info=True)

    stats = DomainStats(
        domain=domain,
        fetched=history.fetched,
        kept=history.accepted,
        complete=complete,
    )
    return stats, history, error


async def run_history(
    cfg: RunConfig,
    *,
    on_progress: ProgressCallback | None = None,
) -> HistoryRunResult:
    """Collect capture-level metadata and aggregate it per normalized endpoint."""
    limits = httpx.Limits(
        max_connections=max(10, cfg.concurrency * 4),
        max_keepalive_connections=max(10, cfg.concurrency * 2),
    )
    rate_limiter = RateLimiter(cfg.rps) if cfg.rps > 0 else None
    sem = asyncio.Semaphore(max(1, cfg.concurrency))
    budget = Budget(cfg.max_results)

    async def guarded(domain: str):
        async with sem:
            if budget.exhausted:
                empty = DomainHistory(domain=domain)
                return DomainStats(domain, 0, 0, complete=False), empty, None
            return await _process_domain(
                domain,
                cfg,
                client=client,
                rate_limiter=rate_limiter,
                on_progress=on_progress,
                budget=budget,
            )

    async with httpx.AsyncClient(**client_kwargs(cfg.http.proxy, limits)) as client:
        tasks = [asyncio.create_task(guarded(domain)) for domain in cfg.domains]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    out = HistoryRunResult()
    for domain, result in zip(cfg.domains, results):
        if isinstance(result, BrokenPipeError):
            raise result
        if isinstance(result, BaseException):
            out.errors.append((domain, result))  # type: ignore[arg-type]
            continue

        stats, history, error = result
        out.stats.append(stats)
        out.analyses[domain] = history
        if error is not None:
            out.errors.append((domain, error))

    return out


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def records_for(
    history: DomainHistory,
    mode: AnalysisMode,
    *,
    timeline_granularity: TimelineGranularity = "year",
    compare_periods: tuple[str, str] | None = None,
) -> list[dict]:
    """Return deterministic serializable records for one analysis view."""
    if mode == "history":
        return [
            {
                "type": "history",
                "domain": history.domain,
                "url": endpoint.url,
                "first_seen": endpoint.first_seen,
                "last_seen": endpoint.last_seen,
                "captures": endpoint.captures,
                "status_codes": _counter_dict(endpoint.status_codes),
                "mime_types": _counter_dict(endpoint.mime_types),
            }
            for endpoint in sorted(history.endpoints.values(), key=lambda item: item.url)
        ]

    if mode == "params":
        return [
            {
                "type": "param",
                "domain": history.domain,
                "parameter": item.parameter,
                "endpoints": item.endpoints,
                "captures": item.captures,
                "first_seen": item.first_seen,
                "last_seen": item.last_seen,
            }
            for item in sorted(history.parameters().values(), key=lambda item: item.parameter)
        ]

    if mode == "timeline":
        return history.timeline(timeline_granularity)

    if mode == "topology":
        return history.topology()

    if mode == "cooccurrence":
        return history.cooccurrence()

    if mode == "changes":
        if compare_periods is None:
            raise ValueError("changes analysis requires two comparison periods")
        return history.changes(*compare_periods)

    params = history.parameters()
    return [
        {
            "type": "summary",
            "domain": history.domain,
            "captures": history.fetched,
            "accepted_captures": history.accepted,
            "unique_urls": len(history.endpoints),
            "unique_parameters": len(params),
            "first_seen": history.first_seen(),
            "last_seen": history.last_seen(),
            "status_codes": _counter_dict(history.status_codes()),
            "mime_types": _counter_dict(history.mime_types()),
        }
    ]


def _compact_counter(value: dict[str, int]) -> str:
    return ",".join(f"{key}:{count}" for key, count in value.items()) or "-"


def format_record(record: dict, fmt: OutputFormat) -> str:
    if fmt == "jsonl":
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))

    kind = record["type"]
    if kind == "history":
        return "\t".join(
            [
                record["url"],
                record["first_seen"] or "-",
                record["last_seen"] or "-",
                str(record["captures"]),
                _compact_counter(record["status_codes"]),
                _compact_counter(record["mime_types"]),
            ]
        )
    if kind == "param":
        return "\t".join(
            [
                record["parameter"],
                str(record["endpoints"]),
                str(record["captures"]),
                record["first_seen"] or "-",
                record["last_seen"] or "-",
            ]
        )
    if kind == "timeline":
        return "\t".join(
            [
                record["period"],
                str(record["captures"]),
                str(record["unique_urls"]),
                str(record["new_urls"]),
                str(record["unique_parameters"]),
                str(record["new_parameters"]),
            ]
        )
    if kind == "topology":
        parameter_sets = (
            ";".join(
                f"{','.join(item['parameters']) or '-'}:{item['unique_urls']}:{item['captures']}"
                for item in record["parameter_sets"]
            )
            or "-"
        )
        return "\t".join(
            [
                record["host"],
                record["path"],
                ",".join(record["schemes"]) or "-",
                str(record["captures"]),
                str(record["unique_urls"]),
                str(record["unique_parameters"]),
                ",".join(record["parameters"]) or "-",
                str(len(record["parameter_sets"])),
                parameter_sets,
                record["first_seen"] or "-",
                record["last_seen"] or "-",
            ]
        )
    if kind == "cooccurrence":
        return "\t".join(
            [
                record["parameters"][0],
                record["parameters"][1],
                str(record["unique_urls"]),
                str(record["routes"]),
                str(record["captures"]),
                record["first_seen"] or "-",
                record["last_seen"] or "-",
            ]
        )
    if kind == "change_summary":
        urls = record["urls"]
        params = record["parameters"]
        return "\t".join(
            [
                "summary",
                record["baseline"],
                record["comparison"],
                f"urls={urls['baseline']}->{urls['comparison']}",
                f"url_added={urls['added']}",
                f"url_removed={urls['removed']}",
                f"url_persisted={urls['persisted']}",
                f"parameters={params['baseline']}->{params['comparison']}",
                f"parameter_added={params['added']}",
                f"parameter_removed={params['removed']}",
                f"parameter_persisted={params['persisted']}",
            ]
        )
    if kind == "change":
        return "\t".join(
            [
                record["baseline"],
                record["comparison"],
                record["entity"],
                record["status"],
                record["value"],
            ]
        )
    return "\t".join(
        [
            record["domain"],
            f"captures={record['captures']}",
            f"accepted={record['accepted_captures']}",
            f"unique_urls={record['unique_urls']}",
            f"unique_parameters={record['unique_parameters']}",
            f"first_seen={record['first_seen'] or '-'}",
            f"last_seen={record['last_seen'] or '-'}",
            f"status_codes={_compact_counter(record['status_codes'])}",
            f"mime_types={_compact_counter(record['mime_types'])}",
        ]
    )


def analysis_path(outdir: Path, domain: str, mode: AnalysisMode, fmt: OutputFormat) -> Path:
    safe_domain = quote(domain, safe=".-_")
    extension = "jsonl" if fmt == "jsonl" else "txt"
    return outdir / f"{safe_domain}.{mode}.{extension}"


def write_analysis_files(result: HistoryRunResult, cfg: RunConfig, mode: AnalysisMode) -> None:
    if not cfg.write_files:
        return
    cfg.outdir.mkdir(parents=True, exist_ok=True)
    for domain in cfg.domains:
        history = result.analyses.get(domain)
        if history is None:
            continue
        with open_outfile(analysis_path(cfg.outdir, domain, mode, cfg.out_format)) as fh:
            for record in records_for(
                history,
                mode,
                timeline_granularity=cfg.timeline_granularity,
                compare_periods=cfg.compare_periods,
            ):
                fh.write(format_record(record, cfg.out_format) + "\n")

#!/usr/bin/env python3
"""Read-only concurrent smoke/load check for a HASSLE NFT API deployment."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass


@dataclass
class Result:
    path: str
    status: int
    elapsed_ms: float
    error: str | None = None


def request_once(base_url: str, path: str, timeout: float) -> Result:
    started = time.perf_counter()
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers={"Accept": "application/json", "User-Agent": "hassle-nft-smoke-load/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            response.read(256 * 1024)
            error = None if status == 200 else f"unexpected HTTP {status}"
    except urllib.error.HTTPError as exc:
        status, error = int(exc.code), f"HTTP {exc.code}"
    except Exception as exc:
        status, error = 0, type(exc).__name__
    return Result(path, status, (time.perf_counter() - started) * 1000, error)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--requests", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--max-p95-ms", type=float, default=1500.0)
    args = parser.parse_args()
    if not args.base_url.startswith("https://"):
        parser.error("--base-url must use HTTPS")
    if not 1 <= args.requests <= 10000 or not 1 <= args.concurrency <= 100:
        parser.error("--requests must be 1..10000 and --concurrency 1..100")
    if args.timeout <= 0 or args.max_p95_ms <= 0:
        parser.error("--timeout and --max-p95-ms must be positive")

    paths = ["/health", "/ready", "/api/marketplace/stats"]
    tasks = [paths[index % len(paths)] for index in range(args.requests)]
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(
            lambda path: request_once(args.base_url, path, args.timeout), tasks
        ))
    elapsed = time.perf_counter() - started
    statuses = Counter(str(result.status) for result in results)
    latencies = [result.elapsed_ms for result in results]
    failures = [result for result in results if result.status != 200]
    report = {
        "requests": len(results),
        "concurrency": args.concurrency,
        "duration_seconds": round(elapsed, 3),
        "requests_per_second": round(len(results) / elapsed, 2) if elapsed else 0,
        "status_counts": dict(sorted(statuses.items())),
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "max": round(max(latencies, default=0), 2),
        },
        "failures_by_path": dict(Counter(result.path for result in failures)),
        "error_types": dict(Counter(result.error or "unknown" for result in failures)),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(failures) or report["latency_ms"]["p95"] > args.max_p95_ms)


if __name__ == "__main__":
    raise SystemExit(main())

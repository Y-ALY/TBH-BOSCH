"""Benchmark CLI for the GDPR scanning pipeline.

Usage:
    python scripts/benchmark.py                    # Full comparison on demo_drive_rich
    python scripts/benchmark.py --limit 500        # Quick test on 500 files
    python scripts/benchmark.py --legacy-only      # Only run legacy scanner
    python scripts/benchmark.py --streaming-only   # Only run streaming scanner
    python scripts/benchmark.py --output report.json  # Save results to JSON
    python scripts/benchmark.py --runs 3           # Average over 3 runs
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.benchmark import (
    BenchmarkResult,
    benchmark_legacy,
    benchmark_streaming,
    run_comparison,
    print_report,
)


def _avg_results(results: list[BenchmarkResult]) -> BenchmarkResult:
    """Average multiple benchmark results into one."""
    if not results:
        return BenchmarkResult(scanner_name="unknown", corpus="", file_count=0, total_time_ms=0.0)
    if len(results) == 1:
        return results[0]

    n = len(results)
    first = results[0]
    return BenchmarkResult(
        scanner_name=first.scanner_name,
        corpus=first.corpus,
        file_count=first.file_count,
        total_time_ms=sum(r.total_time_ms for r in results) / n,
        discovery_time_ms=sum(r.discovery_time_ms for r in results) / n,
        delta_time_ms=sum(r.delta_time_ms for r in results) / n,
        io_time_ms=sum(r.io_time_ms for r in results) / n,
        parse_time_ms=sum(r.parse_time_ms for r in results) / n,
        regex_time_ms=sum(r.regex_time_ms for r in results) / n,
        db_write_time_ms=sum(r.db_write_time_ms for r in results) / n,
        ai_time_ms=sum(r.ai_time_ms for r in results) / n,
        files_per_second=sum(r.files_per_second for r in results) / n,
        mb_per_second=sum(r.mb_per_second for r in results) / n,
        peak_memory_mb=sum(r.peak_memory_mb for r in results) / n,
        total_findings=first.total_findings,  # should be same across runs
        error_count=first.error_count,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/benchmark.py",
        description="Benchmark the GDPR scanning pipeline",
    )
    parser.add_argument(
        "--corpus",
        default=str(_PROJECT_ROOT / "demo_drive_rich"),
        help="Path to corpus directory (default: ./demo_drive_rich)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap file count for quick tests",
    )
    parser.add_argument(
        "--legacy-only",
        action="store_true",
        help="Only run legacy scanner benchmark",
    )
    parser.add_argument(
        "--streaming-only",
        action="store_true",
        help="Only run streaming scanner benchmark",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Save results to JSON file",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of runs for averaging (default: 1)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print results as JSON to stdout (no formatted report)",
    )

    args = parser.parse_args()

    corpus_path = args.corpus
    if not Path(corpus_path).exists():
        print(f"Error: corpus path not found: {corpus_path}", file=sys.stderr)
        return 1

    # ── Run benchmarks ──
    if args.legacy_only:
        print(f"Running legacy benchmark ({args.runs} run(s))...")
        legacy_results = []
        for i in range(args.runs):
            if args.runs > 1:
                print(f"  Run {i + 1}/{args.runs}...")
            legacy_results.append(benchmark_legacy(corpus_path, limit=args.limit))
        legacy = _avg_results(legacy_results)

        if args.json_output:
            print(legacy.to_json())
        else:
            _print_single_result(legacy)

        if args.output:
            _save_json(args.output, {"legacy": legacy.to_dict()})
            print(f"Results saved to {args.output}")

        return 0

    if args.streaming_only:
        print(f"Running streaming benchmark ({args.runs} run(s))...")
        streaming_results = []
        for i in range(args.runs):
            if args.runs > 1:
                print(f"  Run {i + 1}/{args.runs}...")
            streaming_results.append(benchmark_streaming(corpus_path, limit=args.limit))
        streaming = _avg_results(streaming_results)

        if streaming.file_count == 0:
            print("Streaming scanner is not yet available.")
            return 0

        if args.json_output:
            print(streaming.to_json())
        else:
            _print_single_result(streaming)

        if args.output:
            _save_json(args.output, {"streaming": streaming.to_dict()})
            print(f"Results saved to {args.output}")

        return 0

    # ── Full comparison ──
    print(f"Running comparison benchmark ({args.runs} run(s))...")
    comparison = None
    for i in range(args.runs):
        if args.runs > 1:
            print(f"  Run {i + 1}/{args.runs}...")
        comp = run_comparison(corpus_path, limit=args.limit)
        if comparison is None:
            comparison = comp
        else:
            # Average into the running comparison
            comparison["speedup"] = (
                (comparison["speedup"] * i + comp["speedup"]) / (i + 1)
            )
            # Average legacy
            comparison["legacy"] = _avg_results([comparison["legacy"], comp["legacy"]])
            # Average streaming (if available)
            if comp["streaming"].file_count > 0:
                comparison["streaming"] = _avg_results(
                    [comparison["streaming"], comp["streaming"]]
                )
            # Recompute verdict
            speedup = comparison["speedup"]
            if speedup >= 2.0:
                comparison["verdict"] = f"PASS — {speedup:.2f}x speedup"
            else:
                comparison["verdict"] = f"FAIL: {speedup:.2f}x speedup (need >= 2.0x)"

    assert comparison is not None

    if args.json_output:
        output = {
            "legacy": comparison["legacy"].to_dict(),
            "streaming": comparison["streaming"].to_dict(),
            "speedup": comparison["speedup"],
            "verdict": comparison["verdict"],
        }
        print(json.dumps(output, indent=2))
    else:
        print_report(comparison)

    if args.output:
        output = {
            "legacy": comparison["legacy"].to_dict(),
            "streaming": comparison["streaming"].to_dict(),
            "speedup": comparison["speedup"],
            "verdict": comparison["verdict"],
        }
        _save_json(args.output, output)
        print(f"Results saved to {args.output}")

    return 0


def _print_single_result(result: BenchmarkResult) -> None:
    """Print a single benchmark result (no comparison)."""
    from src.benchmark import _print_layer_breakdown, _corpus_size_mb, _format_size

    print()
    print("=" * 70)
    print(f"  GDPR Scan Pipeline Benchmark — {result.scanner_name}")
    print(f"  Corpus: {result.corpus} ({result.file_count:,} files, {_format_size(_corpus_size_mb(result.corpus))})")
    print("=" * 70)
    print()
    print(f"  Total Time:    {result.total_time_ms:,.0f} ms")
    print(f"  Files/Second:  {result.files_per_second:.1f}")
    print(f"  MB/Second:     {result.mb_per_second:.3f}")
    print(f"  Peak Memory:   {result.peak_memory_mb:.1f} MB")
    print(f"  Findings:      {result.total_findings}")
    print(f"  Errors:        {result.error_count}")
    print()
    _print_layer_breakdown(result.scanner_name.capitalize(), result)
    print()


def _save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())

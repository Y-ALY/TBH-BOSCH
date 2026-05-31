"""Benchmark runner for the GDPR scanning pipeline.

Measures every layer of the scan pipeline independently, producing
objective performance data. Works without AI API keys.

Usage:
    from src.benchmark import benchmark_legacy, run_comparison, print_report

    result = benchmark_legacy("./strict_drive", limit=100)
    print(f"{result.files_per_second:.1f} files/sec")

    comparison = run_comparison("./strict_drive", limit=500)
    print_report(comparison)
"""

from __future__ import annotations

import json
import time
import tracemalloc
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .connector import LocalSampleRepoConnector
from .scanner import run_full_scan, _extract_fields
from .models import ScanMetrics
from .pdf_parser import parse_pdf
from .classifier import extract_entities, classify_context
from .owner import assign_owners


# ---------------------------------------------------------------------------
# BenchmarkResult
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    """Results of a single benchmark run."""
    scanner_name: str          # "legacy" or "streaming"
    corpus: str                # path to corpus
    file_count: int
    total_time_ms: float
    # Layer timings (when available)
    discovery_time_ms: float = 0.0
    delta_time_ms: float = 0.0
    io_time_ms: float = 0.0
    parse_time_ms: float = 0.0
    regex_time_ms: float = 0.0
    db_write_time_ms: float = 0.0
    ai_time_ms: float = 0.0
    # Throughput
    files_per_second: float = 0.0
    mb_per_second: float = 0.0
    # Memory
    peak_memory_mb: float = 0.0
    # Outcomes
    total_findings: int = 0
    error_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# Memory measurement helper
# ---------------------------------------------------------------------------

def _measure_peak_memory(func, *args, **kwargs):
    """Execute func and return (result, peak_memory_mb)."""
    tracemalloc.start()
    result = func(*args, **kwargs)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, peak / (1024 * 1024)


# ---------------------------------------------------------------------------
# Instrumented legacy scan (per-layer timing)
# ---------------------------------------------------------------------------

def _run_instrumented_legacy(corpus_path: str, limit: int | None) -> dict:
    """Run an instrumented legacy scan that times each pipeline layer.

    Replicates the logic of scanner.run_full_scan() with per-phase timers.
    """
    connector = LocalSampleRepoConnector(corpus_path)

    # ── Phase 1: Discovery ──
    t0 = time.perf_counter()
    all_files = connector.list_files()
    discovery_ms = (time.perf_counter() - t0) * 1000

    if limit is not None:
        all_files = all_files[:limit]

    file_count = len(all_files)
    if file_count == 0:
        return {
            "file_count": 0,
            "total_size_bytes": 0,
            "discovery_ms": discovery_ms,
            "delta_ms": 0.0,
            "io_ms": 0.0,
            "parse_ms": 0.0,
            "regex_ms": 0.0,
            "total_findings": 0,
            "error_count": 0,
        }

    total_size_bytes = sum(f.size_bytes for f in all_files)

    # ── Phases 2-5: Per-file I/O, parse, regex ──
    io_ms = 0.0
    parse_ms = 0.0
    regex_ms = 0.0
    total_findings = 0
    error_count = 0

    for file_meta in all_files:
        # ── I/O: Download ──
        t_io = time.perf_counter()
        try:
            raw_bytes = connector.download_file(file_meta.file_id)
        except Exception:
            error_count += 1
            continue
        io_ms += (time.perf_counter() - t_io) * 1000

        # ── Parse PDF ──
        t_parse = time.perf_counter()
        try:
            pages, needs_ocr = parse_pdf(raw_bytes)
            full_text = "\n".join(p.text for p in pages)
        except Exception:
            error_count += 1
            continue
        parse_ms += (time.perf_counter() - t_parse) * 1000

        # ── Regex: Fields + Entities + Classify + Owners ──
        t_regex = time.perf_counter()
        fields = _extract_fields(full_text) if not needs_ocr else {}
        if not needs_ocr:
            findings = extract_entities(full_text, pages)
            owner_hints = connector.get_owner_hints(file_meta.file_id)
            assign_owners(
                findings,
                owner_hints,
                file_path=file_meta.path,
                fields=fields,
            )
            for f in findings:
                f.file_id = file_meta.file_id
        else:
            findings = []
        regex_ms += (time.perf_counter() - t_regex) * 1000

        total_findings += len(findings)

    return {
        "file_count": file_count,
        "total_size_bytes": total_size_bytes,
        "discovery_ms": discovery_ms,
        "delta_ms": 0.0,
        "io_ms": io_ms,
        "parse_ms": parse_ms,
        "regex_ms": regex_ms,
        "total_findings": total_findings,
        "error_count": error_count,
    }


# ---------------------------------------------------------------------------
# Public benchmark functions
# ---------------------------------------------------------------------------

def benchmark_legacy(
    corpus_path: str,
    limit: int | None = None,
) -> BenchmarkResult:
    """Run the legacy run_full_scan() and measure everything.

    Uses an instrumented scan loop to capture per-layer timings.
    Memory is measured with tracemalloc.
    """
    corpus = str(Path(corpus_path).resolve())

    # Run instrumented scan under memory measurement
    timings, peak_memory_mb = _measure_peak_memory(
        _run_instrumented_legacy, corpus_path, limit
    )

    file_count = timings["file_count"]
    total_size_bytes = timings["total_size_bytes"]
    total_ms = (
        timings["discovery_ms"]
        + timings["delta_ms"]
        + timings["io_ms"]
        + timings["parse_ms"]
        + timings["regex_ms"]
    )

    # Compute throughput
    total_sec = total_ms / 1000.0
    files_per_second = file_count / total_sec if total_sec > 0 else 0.0
    mb_per_second = (total_size_bytes / (1024 * 1024)) / total_sec if total_sec > 0 else 0.0

    return BenchmarkResult(
        scanner_name="legacy",
        corpus=corpus,
        file_count=file_count,
        total_time_ms=total_ms,
        discovery_time_ms=timings["discovery_ms"],
        delta_time_ms=timings["delta_ms"],
        io_time_ms=timings["io_ms"],
        parse_time_ms=timings["parse_ms"],
        regex_time_ms=timings["regex_ms"],
        db_write_time_ms=0.0,
        ai_time_ms=0.0,
        files_per_second=files_per_second,
        mb_per_second=mb_per_second,
        peak_memory_mb=peak_memory_mb,
        total_findings=timings["total_findings"],
        error_count=timings["error_count"],
    )


def benchmark_streaming(
    corpus_path: str,
    limit: int | None = None,
) -> BenchmarkResult:
    """Run the streaming scanner and measure everything.

    Falls back gracefully if streaming scanner is not yet available.
    """
    corpus = str(Path(corpus_path).resolve())

    try:
        from .streaming_scanner import run_streaming_scan  # type: ignore[import-untyped]
    except ImportError:
        return BenchmarkResult(
            scanner_name="streaming",
            corpus=corpus,
            file_count=0,
            total_time_ms=0.0,
            error_count=0,
        )

    # Streaming scanner exists — run it with memory measurement
    metrics, peak_memory_mb = _measure_peak_memory(
        _run_instrumented_streaming, corpus_path, limit, run_streaming_scan
    )

    file_count = metrics["file_count"]
    total_ms = metrics["total_ms"]
    total_size_bytes = metrics.get("total_size_bytes", 0)

    total_sec = total_ms / 1000.0
    files_per_second = file_count / total_sec if total_sec > 0 else 0.0
    mb_per_second = (total_size_bytes / (1024 * 1024)) / total_sec if total_sec > 0 else 0.0

    return BenchmarkResult(
        scanner_name="streaming",
        corpus=corpus,
        file_count=file_count,
        total_time_ms=total_ms,
        discovery_time_ms=metrics.get("discovery_ms", 0.0),
        delta_time_ms=metrics.get("delta_ms", 0.0),
        io_time_ms=metrics.get("io_ms", 0.0),
        parse_time_ms=metrics.get("parse_ms", 0.0),
        regex_time_ms=metrics.get("regex_ms", 0.0),
        db_write_time_ms=metrics.get("db_write_ms", 0.0),
        ai_time_ms=metrics.get("ai_ms", 0.0),
        files_per_second=files_per_second,
        mb_per_second=mb_per_second,
        peak_memory_mb=peak_memory_mb,
        total_findings=metrics.get("total_findings", 0),
        error_count=metrics.get("error_count", 0),
    )


def _run_instrumented_streaming(
    corpus_path: str,
    limit: int | None,
    run_streaming_scan,
) -> dict:
    """Run streaming scanner and extract timing info.

    The streaming scanner accepts (connector, file_refs, options) and returns
    a ScanMetrics object directly.
    """
    from .models import ScanOptions

    connector = LocalSampleRepoConnector(corpus_path)

    # Collect file refs (metadata only, very small) for counting and iteration
    all_refs = list(connector.iter_files())
    if limit is not None:
        all_refs = all_refs[:limit]

    total_size_bytes = sum(fr.size_bytes for fr in all_refs)
    file_count = len(all_refs)

    # Run scan with ai_mode="off" (no AI API keys needed)
    options = ScanOptions(ai_mode="off")

    t0 = time.perf_counter()
    metrics = run_streaming_scan(connector, iter(all_refs), options)
    total_ms = (time.perf_counter() - t0) * 1000

    return {
        "file_count": metrics.files_scanned,
        "total_size_bytes": total_size_bytes,
        "total_ms": total_ms,
        "discovery_ms": metrics.discovery_time_ms,
        "delta_ms": metrics.delta_time_ms,
        "io_ms": metrics.io_time_ms,
        "parse_ms": metrics.parse_time_ms,
        "regex_ms": metrics.regex_time_ms,
        "db_write_ms": metrics.db_write_time_ms,
        "ai_ms": metrics.ai_time_ms,
        "total_findings": metrics.total_findings,
        "error_count": metrics.files_error,
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def run_comparison(
    corpus_path: str,
    limit: int | None = None,
) -> dict:
    """Run both scanners and return comparison results.

    Returns:
        {
            "legacy": BenchmarkResult,
            "streaming": BenchmarkResult,
            "speedup": float,  # streaming_time / legacy_time
            "verdict": str,    # "PASS" if 2x speedup, else "FAIL: {reason}"
        }
    """
    legacy = benchmark_legacy(corpus_path, limit=limit)
    streaming = benchmark_streaming(corpus_path, limit=limit)

    # Compute speedup (streaming total time vs legacy total time)
    # speedup = legacy_time / streaming_time (how many times faster)
    if streaming.file_count == 0:
        return {
            "legacy": legacy,
            "streaming": streaming,
            "speedup": 0.0,
            "verdict": "FAIL: streaming scanner not available",
        }

    if streaming.total_time_ms <= 0:
        return {
            "legacy": legacy,
            "streaming": streaming,
            "speedup": 0.0,
            "verdict": "FAIL: streaming scanner returned zero time",
        }

    speedup = legacy.total_time_ms / streaming.total_time_ms

    if speedup >= 2.0:
        verdict = f"PASS — {speedup:.2f}x speedup"
    else:
        verdict = f"FAIL: {speedup:.2f}x speedup (need >= 2.0x)"

    return {
        "legacy": legacy,
        "streaming": streaming,
        "speedup": speedup,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def print_report(comparison: dict) -> None:
    """Print a formatted benchmark report suitable for pasting into PRs."""
    legacy: BenchmarkResult = comparison["legacy"]
    streaming: BenchmarkResult = comparison["streaming"]
    speedup: float = comparison["speedup"]
    verdict: str = comparison["verdict"]

    corpus_label = f"{legacy.corpus} ({legacy.file_count:,} files, {_format_size(_corpus_size_mb(legacy.corpus))})"
    if streaming.file_count == 0:
        streaming_label = "N/A"
    else:
        streaming_label = f"{streaming.corpus} ({streaming.file_count:,} files)"

    # Compute display values
    def _speedup_str(a: float, b: float) -> str:
        if b == 0:
            return "N/A"
        ratio = a / b
        return f"{ratio:.2f}x"

    def _better_str(a: float, b: float) -> str:
        """For metrics where *higher* is better (files/sec, MB/sec)."""
        if b == 0:
            return "N/A"
        ratio = a / b
        return f"{ratio:.2f}x better"

    def _value_or_na(val: float, unit: str = "", precision: int = 1) -> str:
        if val == 0 and unit:
            return "N/A"
        if unit == "ms":
            return f"{val:,.0f} ms"
        if unit == "MB":
            return f"{val:,.1f} MB"
        return f"{val:,.{precision}f}"

    print()
    print("=" * 70)
    print("  GDPR Scan Pipeline Benchmark")
    print(f"  Corpus: {corpus_label}")
    print("=" * 70)
    print()
    print(f"{'':<20} {'LEGACY':<16} {'STREAMING':<16} {'SPEEDUP'}")
    print("─" * 70)

    # Total Time
    lt = legacy.total_time_ms
    st = streaming.total_time_ms
    print(f"{'Total Time':<20} {_value_or_na(lt, 'ms'):<16} {_value_or_na(st, 'ms'):<16} {_speedup_str(lt, st)}")

    # Files/Second
    lfps = legacy.files_per_second
    sfps = streaming.files_per_second
    print(f"{'Files/Second':<20} {_value_or_na(lfps):<16} {_value_or_na(sfps):<16} {_better_str(sfps, lfps)}")

    # MB/Second
    lmbps = legacy.mb_per_second
    smbps = streaming.mb_per_second
    print(f"{'MB/Second':<20} {_value_or_na(lmbps, precision=3):<16} {_value_or_na(smbps, precision=3):<16} {_better_str(smbps, lmbps)}")

    # Peak Memory
    lmem = legacy.peak_memory_mb
    smem = streaming.peak_memory_mb
    mem_str = _better_str(lmem, smem) if smem > 0 else "N/A"  # lower is better for memory
    print(f"{'Peak Memory':<20} {_value_or_na(lmem, 'MB'):<16} {_value_or_na(smem, 'MB'):<16} {mem_str}")

    print("─" * 70)

    # Findings
    lf = legacy.total_findings
    sf = streaming.total_findings
    find_str = "same" if lf == sf else f"{sf} vs {lf}"
    print(f"{'Findings':<20} {lf:<16} {sf:<16} {find_str}")

    # Errors
    le = legacy.error_count
    se = streaming.error_count
    err_str = "same" if le == se else f"{se} vs {le}"
    print(f"{'Errors':<20} {le:<16} {se:<16} {err_str}")

    print("─" * 70)
    print()

    # Layer Breakdown
    _print_layer_breakdown("Legacy", legacy)
    print()
    _print_layer_breakdown("Streaming", streaming)

    print("=" * 70)
    print(f"  VERDICT: {verdict}")
    print("=" * 70)
    print()


def _print_layer_breakdown(label: str, result: BenchmarkResult) -> None:
    """Print per-layer timing breakdown for a single benchmark result."""
    if result.total_time_ms <= 0:
        print(f"Layer Breakdown ({label}): N/A")
        return

    layers = [
        ("Discovery", result.discovery_time_ms),
        ("Delta", result.delta_time_ms),
        ("I/O", result.io_time_ms),
        ("Parse", result.parse_time_ms),
        ("Regex", result.regex_time_ms),
        ("DB Write", result.db_write_time_ms),
        ("AI", result.ai_time_ms),
    ]

    total = result.total_time_ms
    print(f"Layer Breakdown ({label}):")
    for name, ms in layers:
        pct = (ms / total * 100) if total > 0 else 0.0
        print(f"  {name:<12} {ms:>8,.0f} ms  ({pct:>4.1f}%)")


def _corpus_size_mb(corpus_path: str) -> float:
    """Return total size of PDF files in the corpus in MB."""
    p = Path(corpus_path)
    if not p.exists():
        return 0.0
    total = sum(f.stat().st_size for f in p.glob("*.pdf"))
    return total / (1024 * 1024)


def _format_size(mb: float) -> str:
    if mb < 1:
        return f"{mb * 1024:.0f} KB"
    if mb < 1024:
        return f"{mb:.0f} MB"
    return f"{mb / 1024:.1f} GB"

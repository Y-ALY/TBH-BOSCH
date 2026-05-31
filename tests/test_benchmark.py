"""Tests for src/benchmark.py — Benchmark + Metrics Layer.

Verifies that benchmark_legacy, benchmark_streaming, run_comparison,
memory measurement, and JSON serialization all work correctly.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.benchmark import (
    BenchmarkResult,
    benchmark_legacy,
    benchmark_streaming,
    run_comparison,
    print_report,
    _measure_peak_memory,
)

# Path to demo corpus (same as other tests)
DEMO_CORPUS = os.path.join(os.path.dirname(__file__), "..", "strict_drive")


# ---------------------------------------------------------------------------
# BenchmarkResult dataclass
# ---------------------------------------------------------------------------

def test_benchmark_result_defaults():
    """BenchmarkResult should have sensible defaults."""
    br = BenchmarkResult(
        scanner_name="legacy",
        corpus="/tmp/corpus",
        file_count=100,
        total_time_ms=5000.0,
    )
    assert br.scanner_name == "legacy"
    assert br.file_count == 100
    assert br.total_time_ms == 5000.0
    assert br.discovery_time_ms == 0.0
    assert br.delta_time_ms == 0.0
    assert br.io_time_ms == 0.0
    assert br.parse_time_ms == 0.0
    assert br.regex_time_ms == 0.0
    assert br.db_write_time_ms == 0.0
    assert br.ai_time_ms == 0.0
    assert br.files_per_second == 0.0
    assert br.mb_per_second == 0.0
    assert br.peak_memory_mb == 0.0
    assert br.total_findings == 0
    assert br.error_count == 0


def test_benchmark_result_to_dict():
    """to_dict() should return a plain dict with all fields."""
    br = BenchmarkResult(
        scanner_name="legacy",
        corpus="/tmp/corpus",
        file_count=10,
        total_time_ms=1000.0,
        parse_time_ms=500.0,
        total_findings=42,
    )
    d = br.to_dict()
    assert d["scanner_name"] == "legacy"
    assert d["corpus"] == "/tmp/corpus"
    assert d["file_count"] == 10
    assert d["total_time_ms"] == 1000.0
    assert d["parse_time_ms"] == 500.0
    assert d["total_findings"] == 42


def test_benchmark_result_to_json():
    """to_json() should return valid JSON."""
    br = BenchmarkResult(
        scanner_name="test",
        corpus="/tmp/x",
        file_count=5,
        total_time_ms=100.0,
    )
    js = br.to_json()
    data = json.loads(js)
    assert data["scanner_name"] == "test"
    assert data["file_count"] == 5


# ---------------------------------------------------------------------------
# benchmark_legacy
# ---------------------------------------------------------------------------

def test_benchmark_legacy_on_10_files():
    """benchmark_legacy should work on a small subset of files."""
    result = benchmark_legacy(DEMO_CORPUS, limit=10)
    assert result.scanner_name == "legacy"
    assert result.file_count == 10
    assert result.total_time_ms > 0, "Should have non-zero total time"
    assert result.files_per_second > 0, "Should have non-zero throughput"
    assert result.peak_memory_mb > 0, "Should have non-zero memory"
    assert result.total_findings >= 0, "Should report findings count"
    assert result.error_count >= 0, "Should report error count"


def test_benchmark_legacy_layer_timings():
    """Legacy benchmark should populate per-layer timings."""
    result = benchmark_legacy(DEMO_CORPUS, limit=10)
    assert result.discovery_time_ms >= 0
    assert result.io_time_ms > 0, "I/O time should be non-zero for real files"
    assert result.parse_time_ms > 0, "Parse time should be non-zero for PDFs"
    assert result.regex_time_ms >= 0
    # Layer timings should sum to roughly total_time_ms
    layer_sum = (
        result.discovery_time_ms
        + result.delta_time_ms
        + result.io_time_ms
        + result.parse_time_ms
        + result.regex_time_ms
        + result.db_write_time_ms
        + result.ai_time_ms
    )
    # Allow small rounding difference
    assert abs(layer_sum - result.total_time_ms) < 1.0, (
        f"Layer sum ({layer_sum:.1f}) should match total ({result.total_time_ms:.1f})"
    )


def test_benchmark_legacy_limit_zero():
    """benchmark_legacy with limit=0 should return 0 files."""
    result = benchmark_legacy(DEMO_CORPUS, limit=0)
    assert result.file_count == 0
    assert result.total_time_ms >= 0


def test_benchmark_legacy_on_empty_dir():
    """benchmark_legacy on an empty directory should not crash."""
    with tempfile.TemporaryDirectory() as tmp:
        result = benchmark_legacy(tmp, limit=None)
        assert result.file_count == 0
        assert result.total_time_ms >= 0


# ---------------------------------------------------------------------------
# benchmark_streaming
# ---------------------------------------------------------------------------

def test_benchmark_streaming_on_10_files():
    """benchmark_streaming should work on a small subset or fall back gracefully."""
    result = benchmark_streaming(DEMO_CORPUS, limit=10)
    assert result.scanner_name == "streaming"
    # If streaming scanner exists, file_count > 0; otherwise 0
    if result.file_count > 0:
        assert result.total_time_ms > 0, "Should have non-zero total time"
        assert result.files_per_second > 0, "Should have non-zero throughput"
        assert result.peak_memory_mb > 0, "Should have non-zero memory"
        assert result.total_findings >= 0
        assert result.error_count >= 0
    else:
        # Graceful fallback — scanner not available
        assert result.total_time_ms == 0.0
        assert result.error_count == 0


# ---------------------------------------------------------------------------
# run_comparison
# ---------------------------------------------------------------------------

def test_run_comparison_returns_valid_structure():
    """run_comparison should return dict with legacy, streaming, speedup, verdict."""
    comp = run_comparison(DEMO_CORPUS, limit=10)
    assert "legacy" in comp
    assert "streaming" in comp
    assert "speedup" in comp
    assert "verdict" in comp
    assert isinstance(comp["legacy"], BenchmarkResult)
    assert isinstance(comp["streaming"], BenchmarkResult)
    assert isinstance(comp["speedup"], float)
    assert isinstance(comp["verdict"], str)


def test_run_comparison_legacy_has_data():
    """Legacy result in comparison should have real data."""
    comp = run_comparison(DEMO_CORPUS, limit=10)
    legacy = comp["legacy"]
    assert legacy.file_count == 10
    assert legacy.total_time_ms > 0
    assert legacy.total_findings >= 0


def test_run_comparison_speedup_behavior():
    """Speedup should be 0 when streaming unavailable, positive when available."""
    comp = run_comparison(DEMO_CORPUS, limit=10)
    if comp["streaming"].file_count == 0:
        assert comp["speedup"] == 0.0
        assert "FAIL" in comp["verdict"]
    else:
        # Streaming scanner is available
        assert comp["speedup"] > 0, "Speedup should be positive"
        assert "PASS" in comp["verdict"] or "FAIL" in comp["verdict"]


# ---------------------------------------------------------------------------
# Memory measurement
# ---------------------------------------------------------------------------

def test_measure_peak_memory_returns_positive():
    """_measure_peak_memory should return a positive memory value for real work."""
    def _do_work():
        # Allocate some memory to measure
        data = b"x" * (1024 * 1024)  # 1 MB
        return len(data)

    result, peak_mb = _measure_peak_memory(_do_work)
    assert result == 1024 * 1024
    assert peak_mb > 0, f"Expected positive peak memory, got {peak_mb}"


def test_measure_peak_memory_no_allocation():
    """_measure_peak_memory should work for functions that allocate little."""
    def _trivial():
        return 42

    result, peak_mb = _measure_peak_memory(_trivial)
    assert result == 42
    assert peak_mb >= 0


# ---------------------------------------------------------------------------
# Works without AI API key
# ---------------------------------------------------------------------------

def test_benchmark_works_without_ai_key():
    """Benchmark should work without any AI API key set."""
    # Remove any API keys from environment
    old_keys = {}
    for key in list(os.environ.keys()):
        if "API_KEY" in key or "OPENROUTER" in key or "ANTHROPIC" in key:
            old_keys[key] = os.environ.pop(key)

    try:
        result = benchmark_legacy(DEMO_CORPUS, limit=5)
        assert result.file_count == 5
        assert result.total_time_ms > 0
        assert result.total_findings >= 0
    finally:
        # Restore keys
        for key, val in old_keys.items():
            os.environ[key] = val


def test_comparison_works_without_ai_key():
    """run_comparison should work without any AI API key set."""
    old_keys = {}
    for key in list(os.environ.keys()):
        if "API_KEY" in key or "OPENROUTER" in key or "ANTHROPIC" in key:
            old_keys[key] = os.environ.pop(key)

    try:
        comp = run_comparison(DEMO_CORPUS, limit=5)
        assert comp["legacy"].file_count == 5
    finally:
        for key, val in old_keys.items():
            os.environ[key] = val


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def test_benchmark_result_json_is_valid():
    """BenchmarkResult.to_json() should produce parseable JSON."""
    result = benchmark_legacy(DEMO_CORPUS, limit=10)
    js = result.to_json()
    data = json.loads(js)
    required_fields = [
        "scanner_name", "corpus", "file_count", "total_time_ms",
        "files_per_second", "mb_per_second", "peak_memory_mb",
        "total_findings", "error_count",
    ]
    for field in required_fields:
        assert field in data, f"Missing field: {field}"


def test_comparison_json_serializable():
    """run_comparison results should be JSON-serializable."""
    comp = run_comparison(DEMO_CORPUS, limit=10)
    output = {
        "legacy": comp["legacy"].to_dict(),
        "streaming": comp["streaming"].to_dict(),
        "speedup": comp["speedup"],
        "verdict": comp["verdict"],
    }
    js = json.dumps(output, indent=2)
    data = json.loads(js)
    assert "legacy" in data
    assert "streaming" in data
    assert "speedup" in data
    assert "verdict" in data


# ---------------------------------------------------------------------------
# print_report (smoke test — just ensure no crash)
# ---------------------------------------------------------------------------

def test_print_report_does_not_crash():
    """print_report should not raise an exception."""
    comp = run_comparison(DEMO_CORPUS, limit=10)
    # Capture stdout to avoid cluttering test output
    import io
    import contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        print_report(comp)
    output = f.getvalue()
    assert "GDPR Scan Pipeline Benchmark" in output
    assert "VERDICT" in output

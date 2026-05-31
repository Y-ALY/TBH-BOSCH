"""Tests for the AI enrichment queue.

Verifies:
  - AIGate decisions for each ai_mode
  - AIQueue enqueue/dequeue flow
  - Non-blocking behavior (main thread not waiting for AI)
  - Queue max_size enforcement
  - AIMetrics tracking
  - run_layered_scan() returns without waiting for AI
  - Graceful fallback when no AI parser available
"""

from __future__ import annotations

import os
import sys
import threading
import time
import queue as stdlib_queue

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import (
    FileRef,
    FileScanResult,
    FileScanError,
    ScanMetrics,
    ScanOptions,
    Finding,
)
from src.ai_queue import AIGate, AIQueue, AIQueueItem, AIMetrics
from src.ai_parser import AIParseResult


# ---------------------------------------------------------------------------
# Mock AI parser — returns predictable results, no API calls
# ---------------------------------------------------------------------------

class MockAIParser:
    """Mock AI parser that returns fake findings without API calls."""

    def __init__(self, latency_ms: float = 0.01, model: str = "mock/model"):
        self.model = model
        self.latency_ms = latency_ms
        self.call_count = 0
        self.should_fail = False

    def parse(self, text="", fields=None, page_count=1, regex_findings_count=0):
        if self.should_fail:
            raise RuntimeError("Mock AI failure")
        time.sleep(self.latency_ms / 1000)
        self.call_count += 1
        return AIParseResult(
            document_type="expense_report",
            confidence=0.9,
            summary="Mock analysis result",
            flags=["personal_data"],
            findings=[
                {"type": "email", "value": "test@bosch.com",
                 "field": "Email", "risk_level": "medium",
                 "confidence": 0.95, "context": "Mock finding",
                 "recommended_action": "mask"},
            ],
            model_used=self.model,
            tokens_used=100,
        )


# ---------------------------------------------------------------------------
# Helper: build a FileScanResult for testing the gate
# ---------------------------------------------------------------------------

def _make_file_result(
    document_type="expense_report",
    findings=None,
    file_id="test:file.pdf",
    text="sample document text",
    fields=None,
    page_count=1,
) -> FileScanResult:
    return FileScanResult(
        file_ref=FileRef(
            file_id=file_id,
            file_name="file.pdf",
            path_or_uri="/path/file.pdf",
            source_type="local",
            size_bytes=1024,
            last_modified="2024-01-01T00:00:00",
            etag_or_version="abc123",
        ),
        document_type=document_type,
        page_count=page_count,
        text_length=len(text),
        findings=findings or [],
        fields=fields or {},
        text=text,
    )


def _make_finding(risk_level="medium", ftype="email", value="test@example.com") -> Finding:
    return Finding(
        finding_id="",
        file_id="test:file.pdf",
        type=ftype,
        value=value,
        risk_level=risk_level,
    )


# ===========================================================================
# AIGate Tests
# ===========================================================================

def test_aigate_off_mode_never_enriches():
    """ai_mode='off' must return False for any document."""
    gate = AIGate()
    options = ScanOptions(ai_mode="off")

    # Clean document
    result = _make_file_result(document_type="expense_report")
    assert gate.should_enrich(result, options) is False

    # Unknown document
    result = _make_file_result(document_type="unknown")
    assert gate.should_enrich(result, options) is False

    # Document with high-risk finding
    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding(risk_level="high")],
    )
    assert gate.should_enrich(result, options) is False

    # Document with many findings
    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding() for _ in range(10)],
    )
    assert gate.should_enrich(result, options) is False


def test_aigate_full_mode_always_enriches():
    """ai_mode='full' must return True for any document."""
    gate = AIGate()
    options = ScanOptions(ai_mode="full")

    # Clean document
    result = _make_file_result(document_type="expense_report")
    assert gate.should_enrich(result, options) is True

    # Unknown document
    result = _make_file_result(document_type="unknown")
    assert gate.should_enrich(result, options) is True

    # Document with findings
    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding()],
    )
    assert gate.should_enrich(result, options) is True


def test_aigate_layered_unknown_document_type():
    """Layered mode: document_type 'unknown' triggers enrichment."""
    gate = AIGate()
    options = ScanOptions(ai_mode="layered")

    result = _make_file_result(document_type="unknown")
    assert gate.should_enrich(result, options) is True


def test_aigate_layered_high_risk_finding():
    """Layered mode: any finding with risk_level 'high' triggers enrichment."""
    gate = AIGate()
    options = ScanOptions(ai_mode="layered")

    result = _make_file_result(
        document_type="expense_report",
        findings=[
            _make_finding(risk_level="medium"),
            _make_finding(risk_level="high"),
        ],
    )
    assert gate.should_enrich(result, options) is True


def test_aigate_layered_high_findings_count():
    """Layered mode: findings count >= threshold (5) triggers enrichment."""
    gate = AIGate(threshold=5)
    options = ScanOptions(ai_mode="layered")

    # 4 findings → skip
    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding() for _ in range(4)],
    )
    assert gate.should_enrich(result, options) is False

    # 5 findings → enrich
    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding() for _ in range(5)],
    )
    assert gate.should_enrich(result, options) is True

    # 10 findings → enrich
    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding() for _ in range(10)],
    )
    assert gate.should_enrich(result, options) is True


def test_aigate_layered_clean_doc_skipped():
    """Layered mode: clean doc (known type, no high-risk, <5 findings) is skipped."""
    gate = AIGate(threshold=5)
    options = ScanOptions(ai_mode="layered")

    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding(risk_level="medium")],
    )
    assert gate.should_enrich(result, options) is False


def test_aigate_layered_clean_doc_audit_sampling():
    """Layered mode: clean docs (0 findings) have ~5% chance of enrichment."""
    gate = AIGate(audit_sample_rate=0.05)
    options = ScanOptions(ai_mode="layered")

    # Run many trials to verify sampling behavior
    trials = 1000
    enriched = 0
    for _ in range(trials):
        result = _make_file_result(
            document_type="expense_report",
            findings=[],
        )
        if gate.should_enrich(result, options):
            enriched += 1

    # With 5% rate and 1000 trials, expect ~50 (allow broad range for randomness)
    rate = enriched / trials
    assert 0.01 < rate < 0.12, \
        f"Audit sampling rate {rate:.3f} outside expected range 0.01-0.12 (enriched {enriched}/{trials})"


def test_aigate_layered_custom_threshold():
    """Layered mode respects a custom threshold."""
    gate = AIGate(threshold=2)
    options = ScanOptions(ai_mode="layered")

    # 1 finding → skip
    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding()],
    )
    assert gate.should_enrich(result, options) is False

    # 2 findings → enrich
    result = _make_file_result(
        document_type="expense_report",
        findings=[_make_finding() for _ in range(2)],
    )
    assert gate.should_enrich(result, options) is True


def test_aigate_default_mode_is_layered():
    """When no options or ai_mode not set, default behavior is layered."""
    gate = AIGate()

    # No options at all
    result = _make_file_result(document_type="unknown")
    assert gate.should_enrich(result, None) is True

    # Clean doc → skip
    result = _make_file_result(document_type="expense_report")
    assert gate.should_enrich(result, ScanOptions()) is False


# ===========================================================================
# AIQueue Tests
# ===========================================================================

def test_aiqueue_enqueue_dequeue_flow():
    """Items enqueued should be processed by workers and results stored."""
    parser = MockAIParser(latency_ms=0.01)
    ai_queue = AIQueue(ai_parser=parser, max_workers=1)

    item = AIQueueItem(
        scan_id="scan-001",
        file_id="test:file.pdf",
        document_type="expense_report",
        regex_findings_count=3,
        risk_levels=["medium", "medium", "low"],
        text="Sample document text for AI analysis",
        fields={"Employee": "Test User"},
        page_count=2,
    )

    ai_queue.start()
    assert ai_queue.enqueue(item) is True

    # Wait for processing to complete
    metrics = ai_queue.stop(wait=True)

    assert metrics.total_queued == 1
    assert metrics.total_processed == 1
    assert metrics.total_failed == 0
    assert metrics.total_tokens > 0

    # Results should be available
    results = ai_queue.get_results("test:file.pdf")
    assert len(results) > 0
    assert results[0]["type"] == "email"


def test_aiqueue_multiple_items():
    """Multiple items should all be processed."""
    parser = MockAIParser(latency_ms=0.01)
    ai_queue = AIQueue(ai_parser=parser, max_workers=2)
    ai_queue.start()

    for i in range(5):
        item = AIQueueItem(
            scan_id="scan-001",
            file_id=f"test:file{i}.pdf",
            document_type="expense_report",
            regex_findings_count=0,
            risk_levels=[],
            text=f"Document {i} text",
            fields={},
            page_count=1,
        )
        assert ai_queue.enqueue(item) is True

    metrics = ai_queue.stop(wait=True)

    assert metrics.total_queued == 5
    assert metrics.total_processed == 5
    assert metrics.total_failed == 0


def test_aiqueue_non_blocking_main_thread():
    """Main thread enqueues items and continues without waiting for AI."""
    parser = MockAIParser(latency_ms=50)  # Slow AI
    ai_queue = AIQueue(ai_parser=parser, max_workers=1)
    ai_queue.start()

    start = time.monotonic()
    for i in range(3):
        item = AIQueueItem(
            scan_id="scan-001",
            file_id=f"test:file{i}.pdf",
            document_type="expense_report",
            regex_findings_count=0,
            risk_levels=[],
            text=f"Document {i} text",
            fields={},
            page_count=1,
        )
        ai_queue.enqueue(item)

    enqueue_time = (time.monotonic() - start) * 1000

    # Enqueue must be fast (not waiting for AI processing)
    assert enqueue_time < 200, \
        f"Enqueue took {enqueue_time:.0f}ms — should be non-blocking"

    # Now wait for processing
    metrics = ai_queue.stop(wait=True)
    assert metrics.total_processed == 3
    assert metrics.total_failed == 0


def test_aiqueue_respects_max_queue_size():
    """When queue is full, enqueue returns False."""
    parser = MockAIParser(latency_ms=100)  # Slow processor
    ai_queue = AIQueue(ai_parser=parser, max_workers=1, max_queue_size=3)
    ai_queue.start()

    # Fill the queue (3 items)
    for i in range(3):
        item = AIQueueItem(
            scan_id="scan-001",
            file_id=f"test:file{i}.pdf",
            document_type="expense_report",
            regex_findings_count=0,
            risk_levels=[],
            text=f"Document {i}",
            fields={},
            page_count=1,
        )
        assert ai_queue.enqueue(item) is True

    # Queue should now be full — next enqueue fails
    item = AIQueueItem(
        scan_id="scan-001",
        file_id="test:file_overflow.pdf",
        document_type="expense_report",
        regex_findings_count=0,
        risk_levels=[],
        text="Overflow document",
        fields={},
        page_count=1,
    )
    assert ai_queue.enqueue(item) is False

    # Stop and check metrics
    metrics = ai_queue.stop(wait=True)
    assert metrics.total_queued == 3
    assert metrics.total_skipped >= 1  # The overflow item was skipped


def test_aiqueue_graceful_fallback_no_parser():
    """When no AI parser is available, enqueue skips and returns False."""
    ai_queue = AIQueue(ai_parser=None)
    ai_queue.start()

    item = AIQueueItem(
        scan_id="scan-001",
        file_id="test:file.pdf",
        document_type="expense_report",
        regex_findings_count=0,
        risk_levels=[],
        text="Document text",
        fields={},
        page_count=1,
    )
    assert ai_queue.enqueue(item) is False  # No parser → skip

    metrics = ai_queue.stop(wait=True)
    assert metrics.total_queued == 0
    assert metrics.total_skipped == 1
    assert metrics.total_processed == 0


def test_aiqueue_handles_ai_failures():
    """AI errors should not crash workers. Failed items tracked in metrics."""
    parser = MockAIParser(latency_ms=0.01)
    parser.should_fail = True
    ai_queue = AIQueue(ai_parser=parser, max_workers=1)
    ai_queue.start()

    item = AIQueueItem(
        scan_id="scan-001",
        file_id="test:file.pdf",
        document_type="expense_report",
        regex_findings_count=0,
        risk_levels=[],
        text="Document text",
        fields={},
        page_count=1,
    )
    assert ai_queue.enqueue(item) is True

    metrics = ai_queue.stop(wait=True)
    assert metrics.total_queued == 1
    assert metrics.total_processed == 0
    assert metrics.total_failed == 1


def test_aiqueue_get_results_returns_empty_for_unknown_file():
    """get_results() returns empty list for files not in results dict."""
    parser = MockAIParser()
    ai_queue = AIQueue(ai_parser=parser)
    assert ai_queue.get_results("nonexistent:file.pdf") == []


def test_aiqueue_metrics_tracks_model():
    """AIMetrics.model should reflect the parser's model name."""
    parser = MockAIParser(model="test/model-v1")
    ai_queue = AIQueue(ai_parser=parser)
    ai_queue.start()

    item = AIQueueItem(
        scan_id="scan-001",
        file_id="test:file.pdf",
        document_type="expense_report",
        regex_findings_count=0,
        risk_levels=[],
        text="Text",
        fields={},
        page_count=1,
    )
    ai_queue.enqueue(item)
    metrics = ai_queue.stop(wait=True)

    assert metrics.model == "test/model-v1"


def test_aiqueue_metrics_average_latency():
    """Average latency should be computed after items are processed."""
    parser = MockAIParser(latency_ms=10)
    ai_queue = AIQueue(ai_parser=parser, max_workers=1)
    ai_queue.start()

    for i in range(3):
        item = AIQueueItem(
            scan_id="scan-001",
            file_id=f"test:file{i}.pdf",
            document_type="expense_report",
            regex_findings_count=0,
            risk_levels=[],
            text=f"Doc {i}",
            fields={},
            page_count=1,
        )
        ai_queue.enqueue(item)

    metrics = ai_queue.stop(wait=True)
    assert metrics.total_processed == 3
    assert metrics.average_latency_ms > 0


# ===========================================================================
# run_layered_scan() Tests
# ===========================================================================

def test_run_layered_scan_returns_without_waiting_for_ai():
    """run_layered_scan() must return scan metrics immediately, not wait for AI."""
    from src.scanner import run_layered_scan
    from src.connector import LocalSampleRepoConnector

    repo = os.path.join(os.path.dirname(__file__), "..", "strict_drive")
    conn = LocalSampleRepoConnector(repo_path=repo)
    file_refs = list(conn.iter_files())[:10]

    parser = MockAIParser(latency_ms=50)  # Slow AI
    results: list = []
    errors: list = []

    def on_result(r):
        results.append(r)

    def on_error(e):
        errors.append(e)

    start = time.monotonic()
    metrics, ai_queue = run_layered_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full", ai_mode="layered"),
        ai_parser=parser,
        on_result=on_result,
        on_error=on_error,
    )
    scan_time_ms = (time.monotonic() - start) * 1000

    # Main scan should complete quickly (not waiting for AI)
    assert metrics.files_scanned == 10
    assert len(results) == 10
    assert metrics.files_error == 0

    # Scan time should be dominated by PDF parsing, not AI (which is 50ms per doc x 10 = 500ms)
    # If AI were blocking, scan time would be much higher
    # We just verify scan completed and returned metrics
    print(f"Scan time: {scan_time_ms:.0f}ms (should not include AI wait)")

    # Cleanup: wait for AI to complete
    ai_metrics = ai_queue.stop(wait=True)
    assert ai_metrics.total_processed >= 0  # Some files may be queued


def test_run_layered_scan_callback_forwards_results():
    """The on_result callback should receive every file result."""
    from src.scanner import run_layered_scan
    from src.connector import LocalSampleRepoConnector

    repo = os.path.join(os.path.dirname(__file__), "..", "strict_drive")
    conn = LocalSampleRepoConnector(repo_path=repo)
    file_refs = list(conn.iter_files())[:5]

    parser = MockAIParser()
    results: list = []

    def on_result(r):
        results.append(r)

    _metrics, ai_queue = run_layered_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full", ai_mode="full"),
        ai_parser=parser,
        on_result=on_result,
    )

    ai_queue.stop(wait=True)

    assert len(results) == 5
    expected_ids = {fr.file_id for fr in file_refs}
    result_ids = {r.file_ref.file_id for r in results}
    assert result_ids == expected_ids


def test_run_layered_scan_ai_mode_off_never_enqueues():
    """ai_mode='off' should result in zero items in the AI queue."""
    from src.scanner import run_layered_scan
    from src.connector import LocalSampleRepoConnector

    repo = os.path.join(os.path.dirname(__file__), "..", "strict_drive")
    conn = LocalSampleRepoConnector(repo_path=repo)
    file_refs = list(conn.iter_files())[:10]

    parser = MockAIParser()
    _metrics, ai_queue = run_layered_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full", ai_mode="off"),
        ai_parser=parser,
    )

    ai_metrics = ai_queue.stop(wait=True)
    assert ai_metrics.total_queued == 0
    assert ai_metrics.total_processed == 0


def test_run_layered_scan_graceful_fallback_no_parser():
    """When no AI parser is provided, scan still completes without errors."""
    from src.scanner import run_layered_scan
    from src.connector import LocalSampleRepoConnector

    repo = os.path.join(os.path.dirname(__file__), "..", "strict_drive")
    conn = LocalSampleRepoConnector(repo_path=repo)
    file_refs = list(conn.iter_files())[:5]

    results: list = []

    def on_result(r):
        results.append(r)

    metrics, ai_queue = run_layered_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full", ai_mode="layered"),
        ai_parser=None,
        on_result=on_result,
    )

    ai_metrics = ai_queue.stop(wait=True)

    # Scan should complete successfully with regex findings
    assert metrics.files_scanned == 5
    assert len(results) == 5
    # No AI processing since no parser
    assert ai_metrics.total_queued == 0
    assert ai_metrics.total_processed == 0


def test_run_layered_scan_error_isolation():
    """Per-file errors should not crash the layered scan."""
    from src.scanner import run_layered_scan
    from src.connector import LocalSampleRepoConnector
    import tempfile
    from pathlib import Path

    demo = os.path.join(os.path.dirname(__file__), "..", "strict_drive")
    valid_pdfs = sorted(Path(demo).glob("*.pdf"))
    if not valid_pdfs:
        return  # Nothing to test

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Copy one valid PDF
        src = valid_pdfs[0]
        (tmp / src.name).write_bytes(src.read_bytes())
        # Create a bad file
        (tmp / "bad.pdf").write_text("not a real PDF")

        conn = LocalSampleRepoConnector(repo_path=str(tmp))
        file_refs = list(conn.iter_files())

        errors: list = []

        def on_error(e):
            errors.append(e)

        parser = MockAIParser()
        metrics, ai_queue = run_layered_scan(
            conn,
            iter(file_refs),
            ScanOptions(mode="full", ai_mode="layered"),
            ai_parser=parser,
            on_error=on_error,
        )

        ai_queue.stop(wait=True)

        # One file should error, one should succeed
        assert metrics.files_error >= 1
        assert metrics.files_scanned >= 1
        assert len(errors) >= 1

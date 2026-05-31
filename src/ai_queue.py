"""Asynchronous AI enrichment queue — offloads AI from the scan hot path.

Producer/consumer pattern:
  - Main scan thread: produces AIQueueItems via AIGate
  - Background workers: consume items, call AIParser, store results

The main scan completes without waiting for AI. Interesting documents
enter the queue; clean documents skip AI entirely.
"""

from __future__ import annotations

import queue
import random
import threading
import time
from dataclasses import dataclass

from .models import FileScanResult, ScanOptions, Finding


# ---------------------------------------------------------------------------
# AIQueueItem
# ---------------------------------------------------------------------------

@dataclass
class AIQueueItem:
    """A document queued for AI enrichment."""
    scan_id: str
    file_id: str
    document_type: str       # from regex classification
    regex_findings_count: int
    risk_levels: list[str]   # risk levels of regex findings
    text: str                # document full text
    fields: dict             # extracted fields
    page_count: int
    priority: int = 0        # higher = process sooner


# ---------------------------------------------------------------------------
# AIMetrics
# ---------------------------------------------------------------------------

@dataclass
class AIMetrics:
    """AI processing metrics."""
    total_queued: int = 0
    total_processed: int = 0
    total_skipped: int = 0       # gate said no
    total_failed: int = 0        # AI errors
    total_tokens: int = 0
    total_time_ms: float = 0.0
    model: str = ""
    average_latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# AIGate
# ---------------------------------------------------------------------------

class AIGate:
    """Decides which documents should enter the AI queue."""

    def __init__(self, threshold: int = 5, audit_sample_rate: float = 0.05):
        self.threshold = threshold
        self.audit_sample_rate = audit_sample_rate

    def should_enrich(self, file_result: FileScanResult, options: ScanOptions) -> bool:
        """Gate decision for a single file result.

        - ai_mode="off":    never enrich
        - ai_mode="full":   always enrich
        - ai_mode="layered": enrich if document is interesting
        """
        ai_mode = options.ai_mode if options else "layered"

        if ai_mode == "off":
            return False

        if ai_mode == "full":
            return True

        # ── Layered mode (default production) ──
        # 1. Unknown document type → always enrich
        if file_result.document_type == "unknown":
            return True

        # 2. Any finding with risk_level "high" → enrich
        for finding in file_result.findings:
            if isinstance(finding, Finding) and finding.risk_level == "high":
                return True

        # 3. Findings count meets threshold → enrich
        if len(file_result.findings) >= self.threshold:
            return True

        # 4. Clean-doc audit sampling (5% of clean docs for quality audit)
        if len(file_result.findings) == 0:
            if random.random() < self.audit_sample_rate:
                return True

        return False


# ---------------------------------------------------------------------------
# AIQueue
# ---------------------------------------------------------------------------

class AIQueue:
    """Async AI enrichment queue — producer/consumer pattern.

    Main thread enqueues items. Background worker threads consume
    items and call the AI parser. Results stored in a thread-safe dict.
    """

    def __init__(
        self,
        ai_parser=None,
        max_workers: int = 2,
        max_queue_size: int = 1000,
    ):
        self._ai_parser = ai_parser
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._workers: list[threading.Thread] = []
        self._max_workers = max_workers
        self._results: dict[str, list[dict]] = {}  # file_id -> AI findings
        self._results_lock = threading.Lock()
        self._metrics = AIMetrics()
        self._metrics_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._started = False

    # ── Producer API ────────────────────────────────────────────

    def enqueue(self, item: AIQueueItem) -> bool:
        """Add a document to the AI queue. Returns False if queue full."""
        if self._ai_parser is None:
            with self._metrics_lock:
                self._metrics.total_skipped += 1
            return False

        try:
            self._queue.put(item, block=False)
            with self._metrics_lock:
                self._metrics.total_queued += 1
            return True
        except queue.Full:
            with self._metrics_lock:
                self._metrics.total_skipped += 1
            return False

    # ── Lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        """Start background worker threads that consume the queue."""
        if self._started:
            return
        self._started = True

        if self._ai_parser is not None:
            self._metrics.model = getattr(self._ai_parser, "model", "")

        for i in range(self._max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"ai-queue-worker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    def stop(self, wait: bool = True) -> AIMetrics:
        """Stop workers, optionally wait for completion, return AI metrics.

        When wait=True: waits for all queued items to be processed
        before returning. Workers drain the queue then exit.

        When wait=False: signals workers to stop but does not wait
        for them to finish processing queued items.
        """
        self._stop_event.set()

        for t in self._workers:
            t.join(timeout=30 if wait else 0.1)

        return self._metrics

    # ── Consumer API ────────────────────────────────────────────

    def get_results(self, file_id: str) -> list[dict]:
        """Get AI enrichment results for a specific file."""
        with self._results_lock:
            return list(self._results.get(file_id, []))

    @property
    def metrics(self) -> AIMetrics:
        return self._metrics

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    # ── Internal ────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        """Main worker loop: pull from queue, call AI, store result.

        Workers drain the queue until stop_event is set AND the queue
        is empty. This ensures all queued items are processed on stop().
        """
        while True:
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                if self._stop_event.is_set():
                    # Queue is empty and stop was requested — exit cleanly
                    break
                continue

            self._process_item(item)
            self._queue.task_done()

    def _process_item(self, item: AIQueueItem) -> None:
        """Process a single queue item through the AI parser."""
        if self._ai_parser is None:
            return

        start = time.monotonic()
        try:
            ai_result = self._ai_parser.parse(
                text=item.text,
                fields=item.fields,
                page_count=item.page_count,
                regex_findings_count=item.regex_findings_count,
            )

            elapsed_ms = (time.monotonic() - start) * 1000

            with self._metrics_lock:
                self._metrics.total_processed += 1
                self._metrics.total_time_ms += elapsed_ms
                self._metrics.total_tokens += ai_result.tokens_used
                if self._metrics.total_processed > 0:
                    self._metrics.average_latency_ms = (
                        self._metrics.total_time_ms / self._metrics.total_processed
                    )

            with self._results_lock:
                self._results[item.file_id] = ai_result.findings

        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000
            with self._metrics_lock:
                self._metrics.total_failed += 1
                self._metrics.total_time_ms += elapsed_ms

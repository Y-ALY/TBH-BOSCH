"""
pipeline.py – High-level orchestrator for the Fast Filtering Layer.

``FastFilterPipeline`` wires together the scanner, state manager,
and I/O models into a single call:

    pipeline = FastFilterPipeline(state_manager=SQLiteStateManager())
    for flagged in pipeline.process(documents):
        send_to_review_queue(flagged)

The ``process`` method is a **generator** – it yields results as
they are found, keeping memory usage bounded regardless of corpus size.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Generator, Iterable, List, Optional

from pii_filter.models import DocumentInput, FlaggedDocument, PIIMatch
from pii_filter.pii_scanner import PIIScanner
from pii_filter.state_manager import InMemoryStateManager, StateManager

logger = logging.getLogger(__name__)


class FastFilterPipeline:
    """
    Orchestrates delta-aware, batch PII scanning.

    Responsibilities:
        1. Accept an iterable (list **or** generator) of documents.
        2. Skip documents that haven't changed (via the state manager).
        3. Run the regex scanner on each new / updated document.
        4. Yield a ``FlaggedDocument`` for every document with ≥1 hit.
        5. Update the state ledger so the next run skips unchanged docs.

    Thread-safety:
        The scanner is stateless and safe to share.  The state manager
        should **not** be shared across threads without external locking
        (SQLite's own serialisation is sufficient for separate processes,
        but not for in-process threads hitting the same ``Connection``).
    """

    def __init__(
        self,
        *,
        state_manager: Optional[StateManager] = None,
        scanner: Optional[PIIScanner] = None,
    ) -> None:
        """
        Args:
            state_manager: Delta-scan tracker.  Defaults to an
                           :class:`InMemoryStateManager` (no persistence).
            scanner:       PII regex engine.  Defaults to a vanilla
                           :class:`PIIScanner` with Luhn enabled.
        """
        self._state: StateManager = state_manager or InMemoryStateManager()
        self._scanner: PIIScanner = scanner or PIIScanner()

        # ── Per-run statistics (reset on each ``process`` call) ──
        self._stats_total: int = 0
        self._stats_skipped: int = 0
        self._stats_flagged: int = 0

    # ── Public API ───────────────────────────────────────────

    def process(
        self,
        documents: Iterable[DocumentInput],
    ) -> Generator[FlaggedDocument, None, None]:
        """
        Scan *documents* for PII and yield flagged results.

        This is a **lazy generator** – it pulls one document at a time
        from *documents*, so you can feed it a million-row DB cursor
        without blowing up memory.

        Args:
            documents: Any iterable (list, generator, DB cursor wrapper)
                       of :class:`DocumentInput` instances.

        Yields:
            :class:`FlaggedDocument` for each document containing ≥1
            PII match.
        """
        self._stats_total = 0
        self._stats_skipped = 0
        self._stats_flagged = 0

        for doc in documents:
            self._stats_total += 1

            # ── Delta check ──────────────────────────────────
            if not self._state.needs_processing(doc.document_id, doc.last_modified):
                logger.debug(
                    "SKIP (unchanged): %s [%s]",
                    doc.file_name,
                    doc.document_id[:12],
                )
                self._stats_skipped += 1
                continue

            # ── Scan ─────────────────────────────────────────
            matches: List[PIIMatch] = self._scanner.scan(doc.content)

            # ── Update state *before* yielding so that a crash
            #    after yield doesn't cause a re-flag on restart.
            self._state.mark_processed(doc.document_id, doc.last_modified)

            if matches:
                flagged = FlaggedDocument(
                    document_id=doc.document_id,
                    file_name=doc.file_name,
                    matches=matches,
                    scanned_at=datetime.utcnow(),
                )
                self._stats_flagged += 1
                logger.info("FLAGGED: %s", flagged.summary())
                yield flagged
            else:
                logger.debug(
                    "CLEAN: %s [%s]",
                    doc.file_name,
                    doc.document_id[:12],
                )

        logger.info(
            "Scan complete — total: %d | skipped: %d | flagged: %d",
            self._stats_total,
            self._stats_skipped,
            self._stats_flagged,
        )

    # ── Batch convenience (returns list) ─────────────────────

    def process_batch(
        self,
        documents: Iterable[DocumentInput],
    ) -> List[FlaggedDocument]:
        """
        Non-lazy wrapper around :meth:`process` that materialises
        all results into a list.  Useful when you need random access
        or the corpus fits comfortably in memory.
        """
        return list(self.process(documents))

    # ── Introspection ────────────────────────────────────────

    @property
    def stats(self) -> dict[str, int]:
        """Return counters from the most recent ``process`` run."""
        return {
            "total": self._stats_total,
            "skipped": self._stats_skipped,
            "flagged": self._stats_flagged,
        }

    def reset_state(self) -> None:
        """
        Wipe the delta-scan ledger so the next run re-processes
        every document.  Useful after pattern updates.
        """
        self._state.reset()
        logger.info("State manager reset – next run will be a full scan.")

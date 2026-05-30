#!/usr/bin/env python3
"""
demo.py – End-to-end demonstration of the Fast Filtering Layer.

Run:
    python demo.py

This script feeds synthetic corporate documents through the pipeline
and prints the flagged results.  Run it twice to see delta-scan
skipping in action (documents that haven't changed are not re-scanned).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from pii_filter import DocumentInput, FastFilterPipeline, FlaggedDocument
from pii_filter.state_manager import InMemoryStateManager, SQLiteStateManager

# ── Logging setup ────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("demo")


# ── Synthetic document corpus ────────────────────────────────
DOCUMENTS = [
    DocumentInput(
        document_id="doc-001",
        file_name="employee_onboarding.pdf",
        last_modified=datetime(2026, 5, 28, 9, 0),
        content=(
            "Welcome aboard, Jane Doe! Your corporate email is "
            "jane.doe@acme-corp.com. Please wire your first month's "
            "expenses to IBAN DE89 3704 0044 0532 0130 00. "
            "For urgent matters, call +49 170 1234567."
        ),
    ),
    DocumentInput(
        document_id="doc-002",
        file_name="vendor_contract_delta.pdf",
        last_modified=datetime(2026, 5, 29, 14, 30),
        content=(
            "This agreement between Acme Corp and SupplyChain GmbH "
            "concerns the delivery of 5,000 units of Widget-X. "
            "Payment terms: NET-30 from invoice date. "
            "No personal data present in this section."
        ),
    ),
    DocumentInput(
        document_id="doc-003",
        file_name="customer_feedback_form.txt",
        last_modified=datetime(2026, 5, 30, 8, 15),
        content=(
            "Customer: Max Mustermann\n"
            "Email: max.mustermann@example.de\n"
            "Phone: 0151 23456789\n"
            "Credit Card on file: 4539 1488 0343 6467\n"
            "Feedback: The product quality has improved significantly. "
            "I am very satisfied with the recent batch."
        ),
    ),
    DocumentInput(
        document_id="doc-004",
        file_name="internal_memo.txt",
        last_modified=datetime(2026, 5, 27, 16, 0),
        content=(
            "MEMO: Q2 All-Hands Meeting\n"
            "Date: June 15, 2026\n"
            "Location: Building 7, Conference Room A\n"
            "Agenda: Revenue update, product roadmap, team awards.\n"
            "No personal identifiers in this document."
        ),
    ),
    DocumentInput(
        document_id="doc-005",
        file_name="payroll_export.csv",
        last_modified=datetime(2026, 5, 30, 7, 0),
        content=(
            "id,name,email,iban,phone\n"
            "1,Anna Schmidt,anna.schmidt@bosch.com,"
            "DE44 5001 0517 5407 3249 31,+49 711 400 40990\n"
            "2,Lukas Weber,lukas.weber@bosch.com,"
            "DE27 1007 0024 0066 6440 04,+49 30 18681 0\n"
        ),
    ),
]


def main() -> None:
    # ── Initialise with SQLite persistence ───────────────────
    # Swap to InMemoryStateManager() for a stateless one-shot run.
    state = SQLiteStateManager(db_path="demo_scan_state.db")

    pipeline = FastFilterPipeline(state_manager=state)

    log.info("=" * 60)
    log.info("PASS 1 — Initial full scan")
    log.info("=" * 60)

    flagged: list[FlaggedDocument] = pipeline.process_batch(DOCUMENTS)

    for doc in flagged:
        log.info(doc.summary())
        for match in doc.matches:
            log.info(
                "   ├─ [%s] %s  →  \"%s\"",
                match.pii_type.value.upper(),
                match.matched_value,
                match.snippet[:80],
            )

    log.info("Stats: %s", json.dumps(pipeline.stats, indent=2))

    # ── Second pass: nothing changed → everything skipped ────
    log.info("")
    log.info("=" * 60)
    log.info("PASS 2 — Re-run (expect all documents skipped)")
    log.info("=" * 60)

    flagged_2 = pipeline.process_batch(DOCUMENTS)
    assert len(flagged_2) == 0, "Delta scan should skip unchanged docs!"
    log.info("Stats: %s", json.dumps(pipeline.stats, indent=2))

    # ── Third pass: simulate an updated document ─────────────
    log.info("")
    log.info("=" * 60)
    log.info("PASS 3 — doc-002 updated with PII")
    log.info("=" * 60)

    updated_doc = DocumentInput(
        document_id="doc-002",
        file_name="vendor_contract_delta.pdf",
        last_modified=datetime(2026, 5, 30, 12, 0),  # newer timestamp
        content=(
            "UPDATED — This agreement between Acme Corp and "
            "SupplyChain GmbH now includes a personal guarantor: "
            "Contact: hans.meier@supplychainx.de, +49 221 9876543."
        ),
    )

    flagged_3 = pipeline.process_batch([updated_doc])
    for doc in flagged_3:
        log.info(doc.summary())
    log.info("Stats: %s", json.dumps(pipeline.stats, indent=2))

    state.close()
    log.info("Done.")


if __name__ == "__main__":
    main()

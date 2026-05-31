#!/usr/bin/env python3
"""
demo_ingest.py – Demonstrates the File Ingestion → GDPR Scan pipeline.

Run:
    python demo_ingest.py ./documents      # scan a specific folder
    python demo_ingest.py                  # uses ./strict_drive by default

This script:
    1. Reads .pdf, .docx, and .txt files from a target directory.
    2. Converts each into a DocumentInput (Pydantic model).
    3. Pipes them directly into FastFilterPipeline.process_batch().
    4. Prints every PII finding in a human-readable format.
"""

from __future__ import annotations

import json
import logging
import sys

from pii_filter import FastFilterPipeline, ingest_directory
from pii_filter.state_manager import InMemoryStateManager

# ── Logging setup ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("demo_ingest")


def main() -> None:
    # Determine target folder from CLI args or default
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "./strict_drive"

    log.info("=" * 60)
    log.info("🚀 GDPR File Ingestion + PII Scan")
    log.info("=" * 60)
    log.info("Target directory: %s", target_dir)

    # ── Step 1: Create the pipeline ──────────────────────────
    # Using InMemoryStateManager for a clean run every time.
    # Swap to SQLiteStateManager("scan_state.db") for delta-scan persistence.
    pipeline = FastFilterPipeline(
        state_manager=InMemoryStateManager(),
    )

    # ── Step 2: Pipe ingest_directory() → process_batch() ────
    # This is the key integration point:
    #   ingest_directory()  yields DocumentInput objects (lazy generator)
    #   process_batch()     consumes the generator and returns flagged results
    flagged_docs = pipeline.process_batch(ingest_directory(target_dir))

    # ── Step 3: Display results ──────────────────────────────
    if not flagged_docs:
        log.info("✅ No PII found — all clear!")
    else:
        log.info("")
        log.info("⚠️  Found PII in %d document(s):", len(flagged_docs))
        log.info("")

        for doc in flagged_docs:
            print(f"  📄 {doc.file_name}  ({doc.match_count} match(es))")
            for match in doc.matches:
                print(f"     ├─ [{match.pii_type.value.upper():11s}] {match.matched_value}")
                print(f"     │  Context: \"{match.snippet[:100]}\"")
            print()

    # ── Stats summary ────────────────────────────────────────
    log.info("📊 Pipeline stats: %s", json.dumps(pipeline.stats, indent=2))


if __name__ == "__main__":
    main()

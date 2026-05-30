#!/usr/bin/env python3
"""CLI entry point for the AI Parsing Backend.

Reads scanner/regex findings from a JSON file, runs AI classification on each,
and writes the enriched results to an output JSON file.

Usage:
    python -m backend.ai_parser.run_ai_parse \
        --input scanner_output.json \
        --output classified_results.json

    python -m backend.ai_parser.run_ai_parse \
        --input scanner_output.json \
        --output classified_results.json \
        --mode live

Environment variables:
    AI_PARSER_MODE       "mock" (default) or "live"
    OPENROUTER_API_KEY    Required for live mode
    OPENROUTER_MODEL      Model name (default: openai/gpt-4o)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .ai_parser import AIParser
from .schemas import BatchClassificationResult, ClassificationResult, ScannerFinding

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GDPR AI Parsing Backend — classify scanner findings with AI",
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        type=Path,
        help="Path to scanner output JSON (array of findings or {findings: [...]})",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        type=Path,
        help="Path to write classified results JSON",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["mock", "live"],
        default=None,
        help="Override AI_PARSER_MODE env var",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ── Load input ──────────────────────────────────────────────────────
    if not args.input.exists():
        logger.error("Input file not found: %s", args.input)
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        raw = json.load(f)

    # Support both a raw list and a dict with a "findings" key
    if isinstance(raw, list):
        findings_raw = raw
    elif isinstance(raw, dict) and "findings" in raw:
        findings_raw = raw["findings"]
    else:
        logger.error(
            "Expected a JSON array of findings or an object with a 'findings' key. "
            "Got: %s",
            type(raw).__name__,
        )
        sys.exit(1)

    logger.info("Loaded %d findings from %s", len(findings_raw), args.input)

    # ── Parse into models ───────────────────────────────────────────────
    findings: list[ScannerFinding] = []
    for i, item in enumerate(findings_raw):
        try:
            findings.append(ScannerFinding(**item))
        except Exception:
            logger.exception("Skipping malformed finding at index %d", i)

    logger.info("Parsed %d valid ScannerFinding objects", len(findings))

    # ── Classify ────────────────────────────────────────────────────────
    ai_parser = AIParser(mode=args.mode)
    results: list[ClassificationResult] = ai_parser.classify_batch(findings)

    # ── Stats ───────────────────────────────────────────────────────────
    success = sum(1 for r in results if r.classification_status == "success")
    failed = sum(1 for r in results if r.classification_status == "ai_failed")
    mock = sum(1 for r in results if r.classification_status == "mock")

    batch = BatchClassificationResult(
        results=results,
        total=len(results),
        success_count=success,
        failed_count=failed,
        mock_count=mock,
    )

    # ── Write output ────────────────────────────────────────────────────
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(batch.model_dump(), f, indent=2, ensure_ascii=False)

    logger.info(
        "Wrote %d results to %s (success=%d, mock=%d, failed=%d)",
        batch.total,
        args.output,
        batch.success_count,
        batch.mock_count,
        batch.failed_count,
    )

    # Signal failure via exit code if any classifications failed
    if failed > 0:
        logger.warning("Exiting with code 1 — %d classifications failed", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()

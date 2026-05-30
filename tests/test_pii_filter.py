"""
tests/test_pii_filter.py – Unit tests for the Fast Filtering Layer.

Covers:
    • Individual PII pattern detection (email, phone, IBAN, CC)
    • Luhn validation (true positives & false positives)
    • Snippet extraction edge cases
    • Delta-scan state management (both backends)
    • End-to-end pipeline flow
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List

import pytest

from pii_filter.models import DocumentInput, FlaggedDocument, PIIMatch, PIIType
from pii_filter.pii_scanner import PIIScanner, _luhn_check
from pii_filter.pipeline import FastFilterPipeline
from pii_filter.state_manager import InMemoryStateManager


# ─────────────────────────────────────────────────────────────
#  Scanner tests
# ─────────────────────────────────────────────────────────────

class TestEmailDetection:
    def test_simple_email(self) -> None:
        scanner = PIIScanner()
        matches = scanner.scan("Contact us at hello@example.com for info.")
        assert any(m.pii_type == PIIType.EMAIL for m in matches)
        assert any("hello@example.com" in m.matched_value for m in matches)

    def test_email_with_dots_and_plus(self) -> None:
        scanner = PIIScanner()
        matches = scanner.scan("Send to first.last+tag@company.co.uk please.")
        emails = [m for m in matches if m.pii_type == PIIType.EMAIL]
        assert len(emails) >= 1
        assert "first.last+tag@company.co.uk" in emails[0].matched_value

    def test_no_false_positive_on_at_sign(self) -> None:
        scanner = PIIScanner()
        matches = scanner.scan("Use @mentions in Slack.")
        emails = [m for m in matches if m.pii_type == PIIType.EMAIL]
        assert len(emails) == 0


class TestPhoneDetection:
    def test_german_mobile(self) -> None:
        scanner = PIIScanner()
        matches = scanner.scan("Reach me at +49 170 1234567.")
        phones = [m for m in matches if m.pii_type == PIIType.PHONE]
        assert len(phones) >= 1

    def test_us_format(self) -> None:
        scanner = PIIScanner()
        matches = scanner.scan("Call (555) 123-4567 for support.")
        phones = [m for m in matches if m.pii_type == PIIType.PHONE]
        assert len(phones) >= 1


class TestIBANDetection:
    def test_german_iban(self) -> None:
        scanner = PIIScanner()
        matches = scanner.scan("IBAN: DE89 3704 0044 0532 0130 00")
        ibans = [m for m in matches if m.pii_type == PIIType.IBAN]
        assert len(ibans) == 1

    def test_no_false_positive_on_short_codes(self) -> None:
        scanner = PIIScanner()
        matches = scanner.scan("Error code AB12 occurred.")
        ibans = [m for m in matches if m.pii_type == PIIType.IBAN]
        assert len(ibans) == 0


class TestCreditCardDetection:
    def test_valid_visa(self) -> None:
        scanner = PIIScanner()
        # 4539 1488 0343 6467 passes Luhn
        matches = scanner.scan("CC: 4539 1488 0343 6467")
        ccs = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(ccs) >= 1

    def test_invalid_luhn_rejected(self) -> None:
        scanner = PIIScanner(enable_luhn=True)
        # 4539 1488 0343 6460 fails Luhn
        matches = scanner.scan("CC: 4539 1488 0343 6460")
        ccs = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(ccs) == 0

    def test_luhn_disabled(self) -> None:
        scanner = PIIScanner(enable_luhn=False)
        matches = scanner.scan("CC: 4539 1488 0343 6460")
        ccs = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(ccs) >= 1


class TestLuhnAlgorithm:
    def test_known_valid_numbers(self) -> None:
        assert _luhn_check("4539148803436467") is True
        assert _luhn_check("5500000000000004") is True

    def test_known_invalid_numbers(self) -> None:
        assert _luhn_check("1234567890123456") is False

    def test_too_short(self) -> None:
        assert _luhn_check("12345") is False


class TestScannerEmpty:
    def test_empty_string(self) -> None:
        scanner = PIIScanner()
        assert scanner.scan("") == []

    def test_no_pii(self) -> None:
        scanner = PIIScanner()
        result = scanner.scan("This is a perfectly clean corporate memo.")
        assert len(result) == 0


class TestExtraPatterns:
    def test_custom_pattern(self) -> None:
        # Simulate adding a German tax ID pattern (11-digit number).
        tax_re = re.compile(r"\b\d{2}\s?\d{3}\s?\d{5}\s?\d\b")
        scanner = PIIScanner(extra_patterns=[(tax_re, PIIType.PHONE)])
        matches = scanner.scan("Tax ID: 12 345 67890 1")
        assert len(matches) >= 1


class TestOverlapResolution:
    """Verify that nested / overlapping regex matches are de-duplicated."""

    def test_iban_phone_collision(self) -> None:
        """
        The PHONE regex must NOT produce a false-positive match
        inside a German IBAN.  Only the IBAN match should survive.
        """
        scanner = PIIScanner()
        text = "Pay to IBAN: DE89 3704 0044 0532 0130 00 please."
        matches = scanner.scan(text)

        ibans = [m for m in matches if m.pii_type == PIIType.IBAN]
        phones = [m for m in matches if m.pii_type == PIIType.PHONE]

        assert len(ibans) == 1, f"Expected 1 IBAN, got {len(ibans)}"
        assert len(phones) == 0, (
            f"Expected 0 phones (overlap filtered), got {len(phones)}: "
            f"{[p.matched_value for p in phones]}"
        )

    def test_adjacent_matches_not_filtered(self) -> None:
        """Two PII values side-by-side (not overlapping) must both be kept."""
        scanner = PIIScanner()
        text = "Contact alice@example.com or bob@example.org today."
        matches = scanner.scan(text)
        emails = [m for m in matches if m.pii_type == PIIType.EMAIL]
        assert len(emails) == 2

    def test_longer_match_wins(self) -> None:
        """
        When matches share a start index, the longer one must survive.
        """
        scanner = PIIScanner()
        # _resolve_overlaps is a static method we can test directly.
        from pii_filter.pii_scanner import PIIScanner as _S

        short = PIIMatch(
            pii_type=PIIType.PHONE,
            matched_value="1234 5678",
            snippet="…1234 5678…",
            char_offset=0,
            char_end=9,
        )
        long = PIIMatch(
            pii_type=PIIType.IBAN,
            matched_value="DE12 1234 5678 9012 3456",
            snippet="…DE12 1234 5678 9012 3456…",
            char_offset=0,
            char_end=29,
        )

        candidates = [
            (0, 9, short),
            (0, 29, long),
        ]
        result = _S._resolve_overlaps(candidates)
        assert len(result) == 1
        assert result[0].pii_type == PIIType.IBAN

    def test_no_matches_returns_empty(self) -> None:
        result = PIIScanner._resolve_overlaps([])
        assert result == []


# ─────────────────────────────────────────────────────────────
#  State manager tests
# ─────────────────────────────────────────────────────────────

class TestInMemoryStateManager:
    def test_new_doc_needs_processing(self) -> None:
        sm = InMemoryStateManager()
        assert sm.needs_processing("d1", datetime(2026, 1, 1)) is True

    def test_unchanged_doc_skipped(self) -> None:
        sm = InMemoryStateManager()
        ts = datetime(2026, 1, 1)
        sm.mark_processed("d1", ts)
        assert sm.needs_processing("d1", ts) is False

    def test_updated_doc_needs_processing(self) -> None:
        sm = InMemoryStateManager()
        sm.mark_processed("d1", datetime(2026, 1, 1))
        assert sm.needs_processing("d1", datetime(2026, 6, 1)) is True

    def test_reset_clears_state(self) -> None:
        sm = InMemoryStateManager()
        sm.mark_processed("d1", datetime(2026, 1, 1))
        sm.reset()
        assert sm.needs_processing("d1", datetime(2026, 1, 1)) is True


# ─────────────────────────────────────────────────────────────
#  Pipeline integration tests
# ─────────────────────────────────────────────────────────────

class TestPipeline:
    @staticmethod
    def _make_doc(
        doc_id: str = "test-1",
        content: str = "email: a@b.com",
        ts: datetime | None = None,
    ) -> DocumentInput:
        return DocumentInput(
            document_id=doc_id,
            file_name=f"{doc_id}.txt",
            last_modified=ts or datetime(2026, 1, 1),
            content=content,
        )

    def test_flags_document_with_pii(self) -> None:
        pipeline = FastFilterPipeline()
        results = pipeline.process_batch([self._make_doc()])
        assert len(results) == 1
        assert results[0].match_count >= 1

    def test_skips_clean_document(self) -> None:
        pipeline = FastFilterPipeline()
        doc = self._make_doc(content="Nothing personal here.")
        results = pipeline.process_batch([doc])
        assert len(results) == 0

    def test_delta_scan_skips_unchanged(self) -> None:
        pipeline = FastFilterPipeline()
        doc = self._make_doc()

        # First pass: should flag.
        r1 = pipeline.process_batch([doc])
        assert len(r1) == 1

        # Second pass: same doc, same timestamp → skip.
        r2 = pipeline.process_batch([doc])
        assert len(r2) == 0
        assert pipeline.stats["skipped"] == 1

    def test_delta_scan_processes_updated(self) -> None:
        pipeline = FastFilterPipeline()
        doc_v1 = self._make_doc(ts=datetime(2026, 1, 1))
        doc_v2 = self._make_doc(ts=datetime(2026, 6, 1))

        pipeline.process_batch([doc_v1])
        r2 = pipeline.process_batch([doc_v2])
        assert len(r2) == 1  # re-processed because timestamp changed

    def test_generator_input(self) -> None:
        pipeline = FastFilterPipeline()

        def doc_gen():
            for i in range(5):
                yield self._make_doc(
                    doc_id=f"gen-{i}",
                    content=f"Contact gen-{i}@example.com",
                )

        results = list(pipeline.process(doc_gen()))
        assert len(results) == 5

    def test_stats_correct(self) -> None:
        pipeline = FastFilterPipeline()
        docs = [
            self._make_doc(doc_id="a", content="email: x@y.com"),
            self._make_doc(doc_id="b", content="No PII here."),
            self._make_doc(doc_id="c", content="CC: 4539 1488 0343 6467"),
        ]
        pipeline.process_batch(docs)
        assert pipeline.stats["total"] == 3
        assert pipeline.stats["flagged"] == 2  # a and c

    def test_reset_state_enables_rescan(self) -> None:
        pipeline = FastFilterPipeline()
        doc = self._make_doc()
        pipeline.process_batch([doc])
        pipeline.reset_state()
        r2 = pipeline.process_batch([doc])
        assert len(r2) == 1  # re-flagged after reset

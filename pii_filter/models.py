"""
models.py – Pydantic data-models for the Fast Filtering Layer.

Three core models:
    DocumentInput   – what the pipeline *receives* (document metadata + text).
    PIIMatch        – a single pattern hit inside a document.
    FlaggedDocument – the pipeline *output*: one per document that contains ≥1 hit.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── PII category enum ────────────────────────────────────────
class PIIType(str, enum.Enum):
    """Enumeration of the PII categories the scanner can detect."""

    EMAIL = "email"
    PHONE = "phone"
    IBAN = "iban"
    CREDIT_CARD = "credit_card"


# ── Input model ──────────────────────────────────────────────
class DocumentInput(BaseModel):
    """
    Represents a single document entering the pipeline.

    Attributes:
        document_id:   Unique, stable identifier (e.g. SHA-256 of filepath).
        file_name:     Human-readable filename or path.
        last_modified: Filesystem or CMS modification timestamp.
        content:       Full plaintext body of the document.
    """

    document_id: str = Field(
        ...,
        min_length=1,
        description="Stable unique identifier for the document.",
    )
    file_name: str = Field(
        ...,
        min_length=1,
        description="Original filename or relative path.",
    )
    last_modified: datetime = Field(
        ...,
        description="Last-modified timestamp (ISO-8601).",
    )
    content: str = Field(
        ...,
        description="Full plaintext content extracted from the document.",
    )


# ── Per-match output ────────────────────────────────────────
class PIIMatch(BaseModel):
    """
    A single PII match within a document.

    Stores the *type* of PII found, the raw matched value,
    and a surrounding text snippet for human review.
    """

    pii_type: PIIType = Field(
        ...,
        description="Category of PII detected.",
    )
    matched_value: str = Field(
        ...,
        description="The raw string that triggered the match.",
    )
    snippet: str = Field(
        ...,
        max_length=200,
        description=(
            "Up to ~200 chars of surrounding context so a reviewer "
            "can assess the match without opening the full document."
        ),
    )
    char_offset: int = Field(
        ...,
        ge=0,
        description="Character offset of the match start within the document.",
    )
    char_end: int = Field(
        ...,
        ge=0,
        description="Character offset of the match end (exclusive) within the document.",
    )


# ── Flagged-document output ─────────────────────────────────
class FlaggedDocument(BaseModel):
    """
    Aggregated output for a single document that contains ≥1 PII match.

    This is the primary artefact consumed by downstream stages
    (human review queue, vector-DB enrichment, audit log, etc.).
    """

    document_id: str = Field(
        ...,
        description="Mirrors DocumentInput.document_id.",
    )
    file_name: str = Field(
        ...,
        description="Mirrors DocumentInput.file_name.",
    )
    matches: List[PIIMatch] = Field(
        default_factory=list,
        description="All PII matches found in this document.",
    )
    scanned_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC timestamp of when the scan was performed.",
    )

    # ── Convenience helpers ──────────────────────────────────
    @property
    def pii_types_found(self) -> List[PIIType]:
        """De-duplicated list of PII categories detected."""
        return list({m.pii_type for m in self.matches})

    @property
    def match_count(self) -> int:
        return len(self.matches)

    def summary(self) -> str:
        """One-liner for log / CLI output."""
        types = ", ".join(t.value for t in self.pii_types_found)
        return (
            f"[{self.document_id[:12]}…] {self.file_name} → "
            f"{self.match_count} hit(s): {types}"
        )

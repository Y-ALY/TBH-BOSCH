"""Data contracts for the GDPR scanning pipeline.

All modules import from here. Dataclasses map directly to the JSON schemas
defined in the backend plan. No external dependencies beyond stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
import uuid


# ---------------------------------------------------------------------------
# File-level models
# ---------------------------------------------------------------------------

@dataclass
class FileMetadata:
    """What the connector returns for each file in the source."""
    file_id: str
    file_name: str
    path: str
    size_bytes: int
    last_modified: str          # ISO-8601
    content_hash: str           # SHA-256 hex
    mime_type: str = "application/pdf"


@dataclass
class PageContent:
    """A single page of extracted text."""
    page_number: int
    text: str
    char_count: int = 0

    def __post_init__(self):
        if not self.char_count:
            self.char_count = len(self.text)


# ---------------------------------------------------------------------------
# Parsed document (output of full scan per file)
# ---------------------------------------------------------------------------

@dataclass
class ParsedDocument:
    """Full parse result for one file — matches the spec JSON schema."""
    file_id: str
    file_name: str
    source_type: str
    document_type: str = "unknown"
    page_count: int = 0
    text_length: int = 0
    content_hash: str = ""
    owner_hints: dict = field(default_factory=dict)
    needs_ocr: bool = False
    pages: list[PageContent] = field(default_factory=list)
    fields: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Finding — one discovered PII / risk item
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """One discovered entity or risk item in a document."""
    finding_id: str
    file_id: str
    type: str                   # email | employee_id | tax_id | address | signature | name
    value: str
    field: str = ""
    context: str = "unknown"
    risk_level: str = "medium"  # high | medium | low
    confidence: float = 1.0
    evidence: str = ""
    recommended_action: str = "review"
    # Owner assignment (populated by owner.py)
    assigned_owner: str = ""
    owner_email: str = ""
    owner_department: str = ""
    owner_resolved: bool = False
    escalation_target: str = ""

    def __post_init__(self):
        if not self.finding_id:
            self.finding_id = f"finding-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Review queue models
# ---------------------------------------------------------------------------

ALLOWED_REVIEW_ACTIONS = [
    "retain", "delete", "archive", "mask",
    "false_positive", "escalate_dpo",
]


@dataclass
class ReviewAction:
    """One recorded action on a review item."""
    action: str
    reviewer: str
    reason: str
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if self.action not in ALLOWED_REVIEW_ACTIONS:
            raise ValueError(
                f"Invalid action '{self.action}'. Allowed: {ALLOWED_REVIEW_ACTIONS}"
            )


@dataclass
class ReviewItem:
    """A finding queued for human review."""
    review_id: str
    finding_id: str
    assigned_owner: str = ""
    owner_status: str = "pending"    # pending | resolved | escalated | unresolved
    status: str = "pending"          # pending | completed
    allowed_actions: list[str] = field(default_factory=lambda: list(ALLOWED_REVIEW_ACTIONS))
    actions_log: list[ReviewAction] = field(default_factory=list)

    def __post_init__(self):
        if not self.review_id:
            self.review_id = f"review-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Delta / state models
# ---------------------------------------------------------------------------

@dataclass
class FileSnapshot:
    """Per-file state saved for delta comparison."""
    file_id: str
    content_hash: str
    last_modified: str
    change_token: str = ""


@dataclass
class DeltaState:
    """Snapshot of an entire scan, persisted to JSON."""
    scan_id: str
    timestamp: str
    connector_type: str
    files: dict[str, FileSnapshot] = field(default_factory=dict)  # keyed by file_id


# ---------------------------------------------------------------------------
# Top-level results
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """Output of a full scan."""
    scan_id: str
    timestamp: str
    connector_type: str
    files_scanned: int = 0
    change_token: str = ""
    parsed_documents: list[ParsedDocument] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def __post_init__(self):
        if not self.scan_id:
            self.scan_id = f"scan-{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class DeltaReport:
    """Output of a delta scan comparing two states."""
    scan_id: str
    previous_scan_id: str
    timestamp: str
    added: list[FileMetadata] = field(default_factory=list)
    modified: list[FileMetadata] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    missing: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.scan_id:
            self.scan_id = f"delta-{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    @property
    def needs_full_scan(self) -> bool:
        return len(self.added) > 0 or len(self.modified) > 0

"""Data contracts for the GDPR scanning pipeline.

All modules import from here. Dataclasses map directly to the JSON schemas
defined in the backend plan. No external dependencies beyond stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, BinaryIO, Iterator
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
    is_flagged: bool = True
    flag_type: str = "Regex_Match"

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


# ---------------------------------------------------------------------------
# Optimized pipeline models (Agent 0 — Shared Contracts)
# ---------------------------------------------------------------------------

@dataclass
class FileRef:
    """Lightweight file reference — used everywhere instead of full FileMetadata."""
    file_id: str
    file_name: str
    path_or_uri: str
    source_type: str          # "local", "onedrive", "sharepoint", "googledrive"
    size_bytes: int
    last_modified: str        # ISO-8601
    etag_or_version: str      # eTag, cTag, version, or mtime-based token
    mime_type: str = "application/pdf"


@dataclass
class ScanOptions:
    """Configuration for a scan run."""
    mode: str = "delta"        # "delta" | "full"
    strict_hash: bool = False  # If True, compute content hash for changed candidates
    ai_mode: str = "layered"   # "off" | "layered" | "full"
    max_workers: int | None = None


@dataclass
class FileScanResult:
    """Per-file scan output — emitted one at a time, never accumulated in a list."""
    file_ref: FileRef
    document_type: str = "unknown"
    page_count: int = 0
    text_length: int = 0
    needs_ocr: bool = False
    findings: list = field(default_factory=list)  # list[Finding]
    fields: dict = field(default_factory=dict)
    owner_hints: dict = field(default_factory=dict)
    parse_time_ms: float = 0.0
    regex_time_ms: float = 0.0
    io_time_ms: float = 0.0
    text: str = ""              # Full document text (for AI enrichment queue)
    error: str | None = None  # If set, this file had a non-fatal error

    @property
    def is_error(self) -> bool:
        return self.error is not None


@dataclass
class FileScanError:
    """Non-fatal per-file error."""
    file_id: str
    file_name: str
    error_type: str   # "parse_error", "permission_denied", "download_error", "timeout"
    message: str


@dataclass
class ScanMetrics:
    """Aggregated scan performance metrics."""
    scan_id: str = ""
    total_files: int = 0
    files_queued: int = 0
    files_skipped: int = 0
    files_scanned: int = 0
    files_error: int = 0
    total_findings: int = 0
    # Layer timings (ms)
    discovery_time_ms: float = 0.0
    delta_time_ms: float = 0.0
    io_time_ms: float = 0.0
    parse_time_ms: float = 0.0
    regex_time_ms: float = 0.0
    db_write_time_ms: float = 0.0
    ai_time_ms: float = 0.0
    total_time_ms: float = 0.0
    # Memory
    peak_memory_mb: float = 0.0
    # Throughput
    files_per_second: float = 0.0
    mb_per_second: float = 0.0
    # Ratios
    skip_ratio: float = 0.0


@dataclass
class ScanJob:
    """Long-running scan job tracked in DB."""
    scan_id: str
    status: str = "pending"   # pending | running | completed | failed | interrupted
    options: dict = field(default_factory=dict)
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    metrics: ScanMetrics | None = None
    error_count: int = 0

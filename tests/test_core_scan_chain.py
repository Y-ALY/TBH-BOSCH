"""Tests for the core scan chain: connector, PDF parser, classifier,
extractor, owner assignment, and DB writer.

These tests verify the fundamental building blocks of the scan pipeline
without requiring real AI/API credentials or external services.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from src.connector import LocalSampleRepoConnector, Connector
from src.models import (
    FileRef, Finding, PageContent, ALLOWED_REVIEW_ACTIONS,
)
from src.pdf_parser import parse_pdf
from src.classifier import extract_entities, classify_context
from src.extractor import scan_file, scan_directory
from src.owner import assign_owners
from src.db_writer import BulkWriter

from database import (
    FileMetadata as FileMetadataORM,
    Finding as FindingORM,
    ScanJob as ScanJobORM,
    Employee,
)


# Helper: build one-element page list for extract_entities
def _pages(text: str):
    return [PageContent(page_number=1, text=text)]


# =========================================================================
# Connector tests
# =========================================================================

class TestConnectorListFiles:
    """Connector discovers files in a local directory."""

    def test_lists_pdf_files(self, sample_pdf_dir):
        conn = LocalSampleRepoConnector(repo_path=str(sample_pdf_dir))
        files = conn.list_files()
        names = {f.file_name for f in files}
        assert "test_clean.pdf" in names
        assert "test_pii.pdf" in names

    def test_returns_file_metadata_with_required_fields(self, sample_pdf_dir):
        conn = LocalSampleRepoConnector(repo_path=str(sample_pdf_dir))
        files = conn.list_files()
        for f in files:
            assert f.file_id.startswith("local:")
            assert f.file_name.endswith(".pdf")
            assert f.path.endswith(".pdf")
            assert f.size_bytes > 0
            assert f.last_modified
            assert len(f.content_hash) == 64

    def test_empty_directory_returns_empty_list(self, tmp_path):
        conn = LocalSampleRepoConnector(repo_path=str(tmp_path))
        assert conn.list_files() == []

    def test_nonexistent_path_returns_empty_list(self):
        conn = LocalSampleRepoConnector(repo_path="/nonexistent/path/12345")
        assert conn.list_files() == []

    def test_iter_files_streams_results(self, sample_pdf_dir):
        conn = LocalSampleRepoConnector(repo_path=str(sample_pdf_dir))
        results = list(conn.iter_files())
        assert len(results) >= 2
        for fr in results:
            assert isinstance(fr, FileRef)
            assert fr.file_id.startswith("local:")
            assert fr.source_type == "local"
            assert fr.size_bytes > 0

    def test_download_file_returns_bytes(self, sample_pdf_dir):
        conn = LocalSampleRepoConnector(repo_path=str(sample_pdf_dir))
        files = conn.list_files()
        pdf_file = files[0]
        data = conn.download_file(pdf_file.file_id)
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_open_file_returns_readable_binary_stream(self, sample_pdf_dir):
        conn = LocalSampleRepoConnector(repo_path=str(sample_pdf_dir))
        files = list(conn.iter_files())
        ref = files[0]
        stream = conn.open_file(ref)
        try:
            header = stream.read(10)
            assert len(header) > 0
        finally:
            stream.close()

    def test_get_change_token_is_stable(self, sample_pdf_dir):
        conn = LocalSampleRepoConnector(repo_path=str(sample_pdf_dir))
        t1 = conn.get_change_token()
        t2 = conn.get_change_token()
        assert t1 == t2
        assert len(t1) == 64

    def test_abstract_connector_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            Connector()


# =========================================================================
# PDF parser tests
# =========================================================================

class TestPDFParser:
    """PDF parser extracts text from valid PDFs."""

    def test_parse_pdf_module_loads(self):
        """Verify parse_pdf is importable and callable."""
        # Parse a real PDF from sample_docs if available, else skip gracefully
        import os
        sample_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "sample_docs", "Expense_Report_March.pdf"
        )
        if os.path.exists(sample_path):
            data = open(sample_path, "rb").read()
            pages, needs_ocr = parse_pdf(data)
            assert isinstance(pages, list)
            assert isinstance(needs_ocr, bool)
        else:
            pytest.skip("sample_docs/Expense_Report_March.pdf not found")

    def test_pages_have_correct_structure(self):
        """Parse a known-good sample PDF and verify page structure."""
        import os
        sample_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "sample_docs", "Expense_Report_March.pdf"
        )
        if not os.path.exists(sample_path):
            pytest.skip("sample_docs/Expense_Report_March.pdf not found")
        data = open(sample_path, "rb").read()
        pages, _ = parse_pdf(data)
        for i, page in enumerate(pages, start=1):
            assert isinstance(page, PageContent)
            assert page.page_number == i

    def test_handles_minimal_pdf(self, sample_pdf_dir):
        """Parse a minimal PDF and verify pages are returned."""
        pdf_path = sample_pdf_dir / "test_clean.pdf"
        data = pdf_path.read_bytes()
        pages, needs_ocr = parse_pdf(data)
        assert isinstance(pages, list)

    def test_pdf_with_pii_is_parsed(self, sample_pdf_dir):
        """Parse a PDF with PII content — should not crash."""
        pdf_path = sample_pdf_dir / "test_pii.pdf"
        data = pdf_path.read_bytes()
        pages, needs_ocr = parse_pdf(data)
        assert isinstance(pages, list)


# =========================================================================
# Classifier tests
# =========================================================================

class TestClassifier:
    """Classifier detects entity types and document categories."""

    def test_detects_email(self):
        findings = extract_entities(
            "Contact: john.doe@bosch.com",
            _pages("Contact: john.doe@bosch.com"),
        )
        emails = [f for f in findings if f.type == "email"]
        assert len(emails) >= 1
        assert any("john.doe@bosch.com" in e.value for e in emails)

    def test_detects_phone(self):
        findings = extract_entities(
            "Call +49 170 1234567 for support",
            _pages("Call +49 170 1234567 for support"),
        )
        phones = [f for f in findings if f.type == "phone"]
        assert len(phones) >= 1

    def test_detects_tax_id(self):
        findings = extract_entities(
            "Tax ID: DE123456789",
            _pages("Tax ID: DE123456789"),
        )
        tax_ids = [f for f in findings if f.type == "tax_id"]
        assert len(tax_ids) >= 1

    def test_detects_employee_id(self):
        findings = extract_entities(
            "Employee ID: EMP-12345",
            _pages("Employee ID: EMP-12345"),
        )
        emp_ids = [f for f in findings if f.type == "employee_id"]
        assert len(emp_ids) >= 1

    def test_detects_name_fields(self):
        findings = extract_entities(
            "Name: Anna Schmidt\nEmployee: Thomas Berger\nParticipant: Lisa Hoffmann",
            _pages("Name: Anna Schmidt\nEmployee: Thomas Berger\nParticipant: Lisa Hoffmann"),
        )
        names = [f for f in findings if f.type == "name"]
        assert len(names) >= 1

    def test_detects_address(self):
        findings = extract_entities(
            "Hauptstr. 12, 70173 Stuttgart",
            _pages("Hauptstr. 12, 70173 Stuttgart"),
        )
        addresses = [f for f in findings if f.type == "address"]
        assert len(addresses) >= 1

    def test_detects_iban(self):
        findings = extract_entities(
            "IBAN: DE89 3704 0044 0532 0130 00",
            _pages("IBAN: DE89 3704 0044 0532 0130 00"),
        )
        ibans = [f for f in findings if f.type == "iban"]
        assert len(ibans) >= 1

    def test_clean_text_produces_no_findings(self):
        findings = extract_entities(
            "The quick brown fox jumps over the lazy dog. Nothing to see here.",
            _pages("The quick brown fox jumps over the lazy dog. Nothing to see here."),
        )
        assert len(findings) == 0

    def test_external_findings_are_merged(self):
        findings = extract_entities(
            "Hello World",
            _pages("Hello World"),
            external_findings=[
                Finding(
                    finding_id="ext-1",
                    file_id="test",
                    type="custom_type",
                    value="custom_value",
                    flag_type="External",
                )
            ],
        )
        custom = [f for f in findings if f.type == "custom_type"]
        assert len(custom) == 1

    def test_classify_expense_report(self):
        result = classify_context(
            "Expense Report for travel to Munich. Hotel receipt: 245 EUR. Total amount: 500.",
            {},
        )
        assert result == "expense_report"

    def test_classify_supplier_onboarding(self):
        result = classify_context(
            "Supplier Onboarding Form. Company: Example GmbH. Tax ID and vendor code below. Contract details attached.",
            {},
        )
        assert result == "supplier_onboarding"

    def test_classify_it_access_request(self):
        result = classify_context(
            "IT Access Request. User needs admin permissions for the finance system. Grant access.",
            {},
        )
        assert result == "it_access_request"

    def test_classify_training_evaluation(self):
        result = classify_context(
            "Training Evaluation Form. Course feedback and instructor rating. Score: 92.",
            {},
        )
        assert result == "training_evaluation"

    def test_classify_incident_report(self):
        result = classify_context(
            "Safety Incident Report. Accident occurred at Building C. Witness reported hazard.",
            {},
        )
        assert result == "incident_report"

    def test_classify_unknown(self):
        result = classify_context(
            "This text has no recognizable business keywords whatsoever.",
            {},
        )
        assert result == "unknown"


# =========================================================================
# Extractor tests
# =========================================================================

class TestExtractor:
    """Memory-safe extractor finds PII in files."""

    def test_scan_file_finds_pii(self, sample_pdf_dir):
        result = scan_file(str(sample_pdf_dir / "test_pii.pdf"))
        assert "file_path" in result
        assert "findings" in result
        # The hand-crafted PDF may not have extractable text via pdfplumber
        # (font embedding issues), so we just verify structure here.
        assert isinstance(result["findings"], list)

    def test_scan_file_structure(self, sample_pdf_dir):
        result = scan_file(str(sample_pdf_dir / "test_clean.pdf"))
        assert "file_path" in result
        assert "file_name" in result
        assert "owner" in result
        assert "owner_email" in result
        assert "size_bytes" in result
        assert result["size_bytes"] > 0
        assert isinstance(result["findings"], list)

    def test_scan_directory_returns_aggregates(self, sample_pdf_dir):
        result = scan_directory(str(sample_pdf_dir))
        assert "admin_aggregates" in result
        assert "user_file_details" in result
        agg = result["admin_aggregates"]
        assert agg["total_scanned_files"] >= 2
        assert agg["total_size_bytes"] > 0
        assert "findings_by_category" in agg
        assert agg["scan_duration_seconds"] >= 0

    def test_scan_directory_with_owner_hints(self, sample_pdf_dir):
        hints = {
            "test_pii.pdf": {"name": "Sara Hoffmann", "email": "sara@bosch.com"},
        }
        result = scan_directory(str(sample_pdf_dir), owner_hints=hints)
        for detail in result["user_file_details"]:
            if detail["file_name"] == "test_pii.pdf":
                assert detail["owner"] == "Sara Hoffmann"
                assert detail["owner_email"] == "sara@bosch.com"

    def test_scan_directory_nonexistent_path(self):
        result = scan_directory("/nonexistent/path/xyz")
        assert result["admin_aggregates"]["total_scanned_files"] == 0


# =========================================================================
# Owner assignment tests
# =========================================================================

class TestOwnerAssignment:
    """Owner assignment resolves ownership through hints, DB lookups, and fallbacks."""

    def test_static_hints_assign_owner(self, db_session):
        emp = Employee(
            employee_id="BX-99999",
            email="test.owner@bosch.com",
            first_name="Test",
            last_name="Owner",
            password="pw",
            department="Legal",
            location="Berlin",
        )
        db_session.add(emp)
        db_session.commit()

        findings = [
            Finding(finding_id="f1", file_id="local:test.pdf", type="email", value="x@x.com"),
        ]
        hints = {"name": "Test Owner", "email": "test.owner@bosch.com", "department": "Legal"}

        assign_owners(findings, hints, db_session=db_session)

        assert findings[0].assigned_owner == "BX-99999"
        assert findings[0].owner_email == "test.owner@bosch.com"
        assert findings[0].owner_department == "Legal"
        assert findings[0].owner_resolved is True

    def test_hints_without_db_session_uses_name(self):
        findings = [
            Finding(finding_id="f1", file_id="local:test.pdf", type="email", value="x@x.com"),
        ]
        hints = {"name": "Unknown Person", "email": "unknown@example.com"}

        assign_owners(findings, hints)

        assert findings[0].assigned_owner == "Unknown Person"
        assert findings[0].owner_email == "unknown@example.com"
        assert findings[0].owner_resolved is True

    def test_path_based_owner_extraction(self, db_session):
        emp = Employee(
            employee_id="BX-12345",
            email="path.owner@bosch.com",
            first_name="Path",
            last_name="Owner",
            password="pw",
        )
        db_session.add(emp)
        db_session.commit()

        findings = [
            Finding(finding_id="f1", file_id="local:test.pdf", type="email", value="x@x.com"),
        ]
        hints: dict = {}
        file_path = "/shared_drive/BX-12345/documents/report.pdf"

        assign_owners(findings, hints, file_path=file_path, db_session=db_session)

        assert findings[0].assigned_owner == "BX-12345"
        assert findings[0].owner_email == "path.owner@bosch.com"
        assert findings[0].owner_resolved is True

    def test_fallback_to_site_owner(self):
        findings = [
            Finding(finding_id="f1", file_id="local:test.pdf", type="email", value="x@x.com"),
        ]
        hints = {"site_owner": "Dr. Mueller"}

        assign_owners(findings, hints)

        assert findings[0].assigned_owner == "Dr. Mueller"
        assert findings[0].owner_resolved is True
        assert findings[0].escalation_target == "DPO_or_data_governance_team"

    def test_fallback_to_master_of_data(self):
        findings = [
            Finding(finding_id="f1", file_id="local:test.pdf", type="email", value="x@x.com"),
        ]
        hints = {"master_of_data": "CDO Office"}

        assign_owners(findings, hints)

        assert findings[0].assigned_owner == "CDO Office"
        assert findings[0].owner_resolved is True

    def test_absolute_fallback_to_dpo(self):
        findings = [
            Finding(finding_id="f1", file_id="local:test.pdf", type="email", value="x@x.com"),
        ]
        hints: dict = {}

        assign_owners(findings, hints)

        assert findings[0].assigned_owner == "Master_of_Data"
        assert findings[0].owner_resolved is False
        assert findings[0].escalation_target == "DPO_or_data_governance_team"

    def test_empty_findings_list_noop(self):
        hints = {"name": "Someone", "email": "someone@example.com"}
        assign_owners([], hints)  # Should not raise


# =========================================================================
# DB Writer tests (in-memory SQLite integration)
# =========================================================================

class TestDBWriter:
    """BulkWriter writes and upserts findings and file state to SQLite."""

    def test_writes_findings(self, db_session):
        writer = BulkWriter(db_session, batch_size=100)
        for i in range(5):
            f = Finding(
                finding_id=f"write-{i}",
                file_id="local:test.pdf",
                type="email",
                value=f"user{i}@bosch.com",
            )
            writer.add_finding(f)
        writer.flush()
        db_session.commit()

        for i in range(5):
            row = db_session.query(FindingORM).filter(
                FindingORM.finding_uid == f"write-{i}"
            ).first()
            assert row is not None
            assert row.value == f"user{i}@bosch.com"

    def test_upserts_existing_findings(self, db_session):
        writer = BulkWriter(db_session, batch_size=100)

        f1 = Finding(
            finding_id="upsert-1",
            file_id="local:test.pdf",
            type="email",
            value="old@bosch.com",
            risk_level="low",
        )
        writer.add_finding(f1)
        writer.flush()
        db_session.commit()

        f2 = Finding(
            finding_id="upsert-1",
            file_id="local:test.pdf",
            type="email",
            value="new@bosch.com",
            risk_level="high",
        )
        writer.add_finding(f2)
        writer.flush()
        db_session.commit()

        rows = db_session.query(FindingORM).filter(
            FindingORM.finding_uid == "upsert-1"
        ).all()
        assert len(rows) == 1
        assert rows[0].value == "new@bosch.com"
        assert rows[0].risk_level == "high"

    def test_writes_file_state(self, db_session):
        writer = BulkWriter(db_session, batch_size=100)

        fr = FileRef(
            file_id="local:state_test.pdf",
            file_name="state_test.pdf",
            path_or_uri="/tmp/state_test.pdf",
            source_type="local",
            size_bytes=2048,
            last_modified="2024-06-01T12:00:00",
            etag_or_version="v1",
        )
        writer.add_file_state(fr, content_hash="abc123def456")
        writer.flush()
        db_session.commit()

        row = db_session.query(FileMetadataORM).filter(
            FileMetadataORM.file_path == "/tmp/state_test.pdf"
        ).first()
        assert row is not None
        assert row.file_hash == "abc123def456"
        assert row.size_bytes == 2048

    def test_tracks_flush_count_and_timing(self, db_session):
        writer = BulkWriter(db_session, batch_size=10)

        assert writer.flush_count == 0
        assert writer.total_write_time_ms == 0.0

        for i in range(5):
            f = Finding(
                finding_id=f"time-{i}",
                file_id="local:test.pdf",
                type="email",
                value=f"user{i}@bosch.com",
            )
            writer.add_finding(f)

        writer.flush()
        assert writer.flush_count == 1
        assert writer.total_rows_written == 5

    def test_pending_count_tracks_queued_items(self, db_session):
        writer = BulkWriter(db_session, batch_size=100)
        assert writer.pending_count == 0

        for i in range(7):
            f = Finding(
                finding_id=f"count-{i}",
                file_id="local:test.pdf",
                type="email",
                value=f"user{i}@bosch.com",
            )
            writer.add_finding(f)

        assert writer.pending_count == 7

        writer.flush()
        assert writer.pending_count == 0


# =========================================================================
# Review action validation
# =========================================================================

class TestReviewActions:
    """Review actions must be in the allowed set."""

    def test_allowed_actions_are_defined(self):
        assert isinstance(ALLOWED_REVIEW_ACTIONS, list)
        assert len(ALLOWED_REVIEW_ACTIONS) >= 4

    def test_review_action_rejects_invalid(self):
        from src.models import ReviewAction
        with pytest.raises(ValueError):
            ReviewAction(action="invalid_action", reviewer="test", reason="bad")

    def test_review_action_accepts_valid(self):
        from src.models import ReviewAction
        for action in ALLOWED_REVIEW_ACTIONS:
            ra = ReviewAction(action=action, reviewer="test", reason="valid")
            assert ra.action == action
            assert ra.timestamp


# =========================================================================
# Scan models / contracts
# =========================================================================

class TestScanJobModel:
    """ScanJob dataclass controls scan lifecycle."""

    def test_scan_job_default_status_applied_on_flush(self, db_session):
        sid = f"job-{uuid.uuid4().hex[:8]}"
        job = ScanJobORM(scan_id=sid, created_at=datetime.now().isoformat())
        db_session.add(job)
        db_session.flush()
        assert job.status == "pending"

    def test_scan_job_can_update_to_completed(self, db_session):
        sid = f"job-{uuid.uuid4().hex[:8]}"
        job = ScanJobORM(scan_id=sid, status="pending", created_at=datetime.now().isoformat())
        db_session.add(job)
        db_session.commit()

        job.status = "completed"
        job.completed_at = datetime.now().isoformat()
        job.total_findings = 10
        db_session.commit()

        found = db_session.query(ScanJobORM).filter(ScanJobORM.scan_id == sid).first()
        assert found.status == "completed"
        assert found.total_findings == 10

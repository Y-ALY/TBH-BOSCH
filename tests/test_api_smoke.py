"""API smoke tests for the FastAPI applications (main.py and api.py).

Tests use TestClient with in-memory SQLite to avoid any filesystem or
external service dependencies.  No real API keys are needed.

Important: Tests that use both a TestClient fixture and the db_session
fixture in the same function will encounter SAVEPOINT conflicts because
StaticPool shares one connection.  Instead, tests that need to seed data
ALONGSIDE API calls should get a fresh session directly from the engine
fixture (bypassing the db_session SAVEPOINT wrapper).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker


# =========================================================================
# main.py -- web dashboard routes
# =========================================================================

class TestMainAppPages:
    """Smoke-test the main.py web dashboard page routes."""

    def test_login_page_loads(self, main_test_client):
        response = main_test_client.get("/")
        assert response.status_code == 200
        assert "login" in response.text.lower() or "Login" in response.text

    def test_login_post_rejects_invalid_credentials(self, main_test_client, engine):
        response = main_test_client.post("/login", data={
            "role": "admin",
            "email": "nobody@bosch.com",
            "password": "wrong",
        })
        # Should re-render login page, not crash
        assert response.status_code == 200

    def test_login_post_with_valid_admin_redirects(self, main_test_client, engine):
        """Login with valid admin credentials should redirect to dashboard."""
        from database import Employee
        # Seed via a fresh session (not db_session, to avoid SAVEPOINT conflict)
        db = sessionmaker(bind=engine)()
        try:
            emp = Employee(
                employee_id="BX-ADMIN",
                email="admin@bosch.com",
                first_name="Admin",
                last_name="User",
                password="password123",
                department="IT",
                location="Stuttgart",
            )
            db.add(emp)
            db.commit()
        finally:
            db.close()

        response = main_test_client.post("/login", data={
            "role": "admin",
            "email": "admin@bosch.com",
            "password": "password123",
        }, follow_redirects=False)
        assert response.status_code == 303
        assert "/admin-dashboard" in response.headers.get("location", "")

    def test_admin_dashboard_redirects_without_cookie(self, main_test_client):
        """Without a session cookie, the admin dashboard redirects to login."""
        main_test_client.cookies.clear()
        response = main_test_client.get("/admin-dashboard", follow_redirects=False)
        assert response.status_code == 307

    def test_employee_dashboard_redirects_without_cookie(self, main_test_client):
        main_test_client.cookies.clear()
        response = main_test_client.get("/employee-dashboard", follow_redirects=False)
        assert response.status_code == 307

    def test_admin_kpis_returns_error_without_auth(self, main_test_client):
        """KPI endpoint requires BX-ADMIN session cookie."""
        main_test_client.cookies.clear()
        response = main_test_client.get("/api/admin/kpis")
        # Without cookie, session_emp_id is None; should return 403
        assert response.status_code in (403, 401, 307)

    def test_search_returns_error_without_auth(self, main_test_client):
        main_test_client.cookies.clear()
        response = main_test_client.get("/api/search")
        # Search requires auth; without cookie should redirect or 401
        assert response.status_code in (403, 401, 307)

    def test_admin_intake_requires_auth(self, main_test_client, tmp_path):
        main_test_client.cookies.clear()
        response = main_test_client.post(
            "/api/admin/intake/link",
            json={"source": str(tmp_path)},
        )
        assert response.status_code == 403

        response = main_test_client.get("/api/admin/extraction-results")
        assert response.status_code == 403

    def test_trigger_extraction_persists_new_text_files(self, main_test_client, engine, tmp_path):
        from database import FileMetadata, Finding

        main_test_client.cookies.set("session_emp_id", "BX-ADMIN")
        source_dir = tmp_path / "intake"
        source_dir.mkdir()
        source_file = source_dir / "employee_record.txt"
        source_file.write_text(
            "Employee: Maya Singh\nEmail: maya.singh@bosch.com\nTax ID: DE123456789\n",
            encoding="utf-8",
        )

        response = main_test_client.post(
            "/api/admin/trigger-extraction",
            json={"target_dir": str(source_dir)},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["admin_aggregates"]["total_scanned_files"] == 1
        assert data["admin_aggregates"]["total_findings"] >= 2
        assert data["db_files_created"] == 1
        assert data["db_findings_added"] >= 2

        db = sessionmaker(bind=engine)()
        try:
            file_row = db.query(FileMetadata).filter(FileMetadata.file_path == str(source_file)).first()
            assert file_row is not None
            assert db.query(Finding).filter(Finding.file_id == file_row.id).count() >= 2
        finally:
            db.close()

    def test_intake_upload_feeds_dashboard_database(self, main_test_client, engine):
        from database import FileMetadata, Finding

        main_test_client.cookies.set("session_emp_id", "BX-ADMIN")
        response = main_test_client.post(
            "/api/admin/intake/upload",
            files=[
                (
                    "files",
                    (
                        "uploaded_pii.txt",
                        b"Contact: upload.owner@bosch.com\nPhone: +49 170 1234567\n",
                        "text/plain",
                    ),
                )
            ],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["admin_aggregates"]["total_scanned_files"] == 1
        assert data["db_files_created"] == 1
        assert data["db_findings_added"] >= 1

        db = sessionmaker(bind=engine)()
        try:
            file_row = db.query(FileMetadata).filter(FileMetadata.file_path.like("%uploaded_pii.txt")).first()
            assert file_row is not None
            assert db.query(Finding).filter(Finding.file_id == file_row.id).count() >= 1
        finally:
            db.close()

    def test_intake_link_scans_local_directory(self, main_test_client, engine, tmp_path):
        from database import FileMetadata, Finding

        main_test_client.cookies.set("session_emp_id", "BX-ADMIN")
        source_dir = tmp_path / "linked-source"
        source_dir.mkdir()
        source_file = source_dir / "linked_pii.txt"
        source_file.write_text(
            "Name: Luca Bauer\nEmail: luca.bauer@bosch.com\nIBAN: DE89 3704 0044 0532 0130 00\n",
            encoding="utf-8",
        )

        response = main_test_client.post(
            "/api/admin/intake/link",
            json={"source": str(source_dir)},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["admin_aggregates"]["total_scanned_files"] == 1
        assert data["admin_aggregates"]["total_findings"] >= 2

        db = sessionmaker(bind=engine)()
        try:
            file_row = db.query(FileMetadata).filter(FileMetadata.file_path == str(source_file)).first()
            assert file_row is not None
            assert db.query(Finding).filter(Finding.file_id == file_row.id).count() >= 2
        finally:
            db.close()


# =========================================================================
# api.py -- scan API routes
# =========================================================================

class TestScanAPI:
    """Smoke-test the api.py scan endpoints."""

    def test_health_check(self, api_test_client):
        response = api_test_client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_list_scans_empty(self, api_test_client):
        response = api_test_client.get("/api/scans")
        assert response.status_code == 200
        data = response.json()
        assert "scans" in data
        assert isinstance(data["scans"], list)

    def test_list_findings_empty(self, api_test_client):
        response = api_test_client.get("/api/findings")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "metadata" in data
        assert isinstance(data["results"], list)

    def test_scan_nonexistent_folder_returns_400(self, api_test_client):
        response = api_test_client.post("/api/scan", json={
            "folder_path": "/nonexistent/folder/xyz123",
        })
        assert response.status_code == 400

    def test_scan_valid_folder_creates_job(self, api_test_client, sample_pdf_dir):
        """Trigger a scan on sample_pdf_dir; verify a scan_id is returned.

        The background thread may not complete within the test window,
        so we only assert that the initial response is successful.
        """
        response = api_test_client.post("/api/scan", json={
            "folder_path": str(sample_pdf_dir),
            "mode": "full",
            "ai_mode": "off",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "scan_id" in data

    def test_get_scan_job_not_found(self, api_test_client):
        response = api_test_client.get("/api/scan/nonexistent-12345")
        assert response.status_code == 404

    def test_findings_with_filters(self, api_test_client, engine):
        """Insert a finding directly, then verify the API can query it."""
        from database import Finding as FindingORM

        db = sessionmaker(bind=engine)()
        try:
            f = FindingORM(
                finding_uid="test-finding-001",
                type="email",
                value="test@bosch.com",
                risk_level="high",
                review_status="pending_review",
                status="pending_review",
                category="email",
                confidence_score=0.95,
                flagged_snippet="test@bosch.com",
                reasoning="Test context",
            )
            db.add(f)
            db.commit()
        finally:
            db.close()

        # Query by risk_level
        response = api_test_client.get("/api/findings?risk_level=high")
        assert response.status_code == 200
        data = response.json()
        assert data["metadata"]["total_count"] >= 1

        # Query by type
        response = api_test_client.get("/api/findings?type=email")
        assert response.status_code == 200

        # Query by search text
        response = api_test_client.get("/api/findings?q=test@bosch")
        assert response.status_code == 200


# =========================================================================
# Review API tests
# =========================================================================

class TestReviewAPI:
    """Test the review action endpoint in api.py."""

    def test_review_invalid_action_returns_422(self, api_test_client):
        response = api_test_client.post("/api/findings/nonexistent/review", json={
            "action": "not_a_real_action",
            "reviewer": "admin",
        })
        assert response.status_code == 422

    def test_review_nonexistent_finding_returns_404(self, api_test_client):
        response = api_test_client.post("/api/findings/nonexistent-12345/review", json={
            "action": "retain",
            "reviewer": "admin",
        })
        assert response.status_code == 404

    def test_review_valid_action_updates_finding(self, api_test_client, engine):
        from database import Finding as FindingORM

        db = sessionmaker(bind=engine)()
        try:
            f = FindingORM(
                finding_uid="review-test-001",
                type="email",
                value="review@bosch.com",
                risk_level="medium",
                review_status="pending_review",
                status="pending_review",
                category="email",
                confidence_score=0.9,
                flagged_snippet="review@bosch.com",
                reasoning="Review test",
            )
            db.add(f)
            db.commit()
        finally:
            db.close()

        response = api_test_client.post("/api/findings/review-test-001/review", json={
            "action": "mask",
            "reviewer": "dpo@bosch.com",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["action"] == "mask"

        # Verify DB update
        db2 = sessionmaker(bind=engine)()
        try:
            updated = db2.query(FindingORM).filter(
                FindingORM.finding_uid == "review-test-001"
            ).first()
            assert updated is not None
            assert updated.review_action == "mask"
            assert updated.reviewer == "dpo@bosch.com"
        finally:
            db2.close()

    def test_review_delete_action(self, api_test_client, engine):
        from database import Finding as FindingORM

        db = sessionmaker(bind=engine)()
        try:
            f = FindingORM(
                finding_uid="delete-test-001",
                type="phone",
                value="+49123456789",
                risk_level="medium",
                review_status="pending_review",
                status="pending_review",
                category="phone",
                confidence_score=0.9,
                flagged_snippet="+49123456789",
                reasoning="Delete test",
            )
            db.add(f)
            db.commit()
        finally:
            db.close()

        response = api_test_client.post("/api/findings/delete-test-001/review", json={
            "action": "delete",
            "reviewer": "admin",
        })
        assert response.status_code == 200

        db2 = sessionmaker(bind=engine)()
        try:
            updated = db2.query(FindingORM).filter(
                FindingORM.finding_uid == "delete-test-001"
            ).first()
            assert updated is not None
            assert updated.review_action == "delete"
            assert updated.review_status == "deleted"
        finally:
            db2.close()


# =========================================================================
# Fake AI parser integration
# =========================================================================

class TestFakeAIParser:
    """Verify the fake AI parser produces deterministic results without API calls."""

    def test_fake_parser_returns_preconfigured_type(self):
        from tests.conftest import FakeAIParser
        parser = FakeAIParser(document_type="expense_report")
        result = parser.parse("some text", fields={})
        assert result.document_type == "expense_report"
        assert result.confidence == 0.85

    def test_fake_parser_returns_custom_findings(self):
        from tests.conftest import FakeAIParser
        from src.models import Finding

        custom = [
            Finding(
                finding_id="ai-1", file_id="local:test.pdf",
                type="email", value="ai-found@bosch.com", flag_type="AI_Match",
            ),
        ]
        parser = FakeAIParser(findings=custom)
        result = parser.parse("text")
        assert len(result.findings) == 1
        assert result.findings[0].type == "email"

    def test_fake_parser_does_not_require_api_key(self):
        from tests.conftest import FakeAIParser
        parser = FakeAIParser()
        result = parser.parse("any text")
        assert result.document_type == "unknown"
        assert result.findings == []

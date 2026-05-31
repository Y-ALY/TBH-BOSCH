"""Shared pytest fixtures for the TBH-BOSCH test suite.

Provides:
- In-memory SQLite database with schema pre-created (StaticPool, shared).
- SQLAlchemy session with SAVEPOINT-based isolation.
- TestClient for main.py FastAPI app (startup cleared, in-memory DB).
- TestClient for api.py FastAPI app (in-memory DB).
- Temporary directory with sample PDF files for file-based tests.
- Fake AI parser that returns deterministic results without API calls.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

# Ensure project root is on sys.path for all test modules.
_proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker, Session

from database import Base, get_db


# ---------------------------------------------------------------------------
# In-memory SQLite database with StaticPool
#
# StaticPool is critical: SQLite :memory: databases are unique per
# connection by default.  Without StaticPool, the TestClient's dependency-
# override sessions and the direct db_session fixture would each see
# separate empty databases.  StaticPool ensures every session shares
# exactly one underlying DBAPI connection.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def engine():
    """Session-scoped in-memory SQLite engine.

    Uses StaticPool so ALL connections (direct sessions AND FastAPI
    dependency-override sessions) share the same in-memory database.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture(scope="function")
def db_session(engine) -> Generator[Session, None, None]:
    """Function-scoped session with SAVEPOINT isolation.

    Uses a nested transaction (SAVEPOINT) so that data written by one test
    is invisible to the next.  Works with StaticPool because the savepoint
    isolates operations within a single test function, and is rolled back
    in teardown regardless of commit/rollback calls in the test body.
    """
    connection = engine.connect()
    transaction = connection.begin_nested()  # SAVEPOINT, not top-level TXN
    session = sessionmaker(bind=connection)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# TestClient for main.py FastAPI app
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_test_client(engine):
    """Module-scoped TestClient for main.py with in-memory DB.

    We clear the startup event handler (which would seed demo_drive_rich/)
    and override get_db to use the test engine.
    """
    from main import app as _main_app
    # Prevent main.py's startup event from running — it tries to seed
    # demo_drive_rich/ which is irrelevant for API smoke tests.
    _main_app.router.on_startup.clear()

    def _test_get_db():
        test_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
        try:
            yield test_session
        finally:
            test_session.close()

    _main_app.dependency_overrides[get_db] = _test_get_db

    from fastapi.testclient import TestClient
    with TestClient(_main_app) as client:
        yield client

    _main_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# TestClient for api.py FastAPI app
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def api_test_client(engine):
    """Module-scoped TestClient for api.py with in-memory DB."""
    from api import app as _api_app

    def _test_get_db():
        test_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
        try:
            yield test_session
        finally:
            test_session.close()

    _api_app.dependency_overrides[get_db] = _test_get_db

    from fastapi.testclient import TestClient
    with TestClient(_api_app) as client:
        yield client

    _api_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Temporary directory with sample PDF files
# ---------------------------------------------------------------------------

def _make_minimal_pdf_bytes(text: str) -> bytes:
    """Create a minimal PDF 1.4 file with the given text embedded."""
    text_bytes = text.encode("utf-8")
    # Compute the stream length by embedding the text into the BT/ET block
    stream_content = b"BT\n/F1 12 Tf\n100 700 Td\n" + text_bytes + b"\nET"
    stream_len = len(stream_content)
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length " + str(stream_len).encode() + b" >>\nstream\n"
        + stream_content +
        b"\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000266 00000 n \n"
        b"0000000372 00000 n \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
        b"startxref\n441\n%%EOF\n"
    )
    return pdf


@pytest.fixture(scope="function")
def sample_pdf_dir(tmp_path):
    """Temporary directory containing two sample PDFs.

    Returns the Path to the directory.  PDFs are:
    - test_clean.pdf: a clean memo with no PII
    - test_pii.pdf: contains email, phone, and tax ID
    """
    clean_pdf = _make_minimal_pdf_bytes(
        "MEMO: Q2 All-Hands Meeting.\n"
        "Agenda: product roadmap and budget review.\n"
        "No personal data in this document."
    )
    pii_pdf = _make_minimal_pdf_bytes(
        "Employee: Sara Hoffmann\n"
        "Email: sara.hoffmann@bosch.com\n"
        "Phone: +49 170 1234567\n"
        "Tax ID: DE123456789\n"
        "Address: Hauptstr. 12, 70173 Stuttgart\n"
    )

    (tmp_path / "test_clean.pdf").write_bytes(clean_pdf)
    (tmp_path / "test_pii.pdf").write_bytes(pii_pdf)
    return tmp_path


# ---------------------------------------------------------------------------
# Fake AI parser (deterministic, no API calls)
# ---------------------------------------------------------------------------

class FakeAIParser:
    """Returns deterministic, pre-configured results without any API calls.

    Usage in tests:
        fake = FakeAIParser(document_type="expense_report")
        result = fake.parse(text, fields)
        assert result.document_type == "expense_report"
    """

    def __init__(
        self,
        document_type: str = "unknown",
        findings: list | None = None,
        summary: str = "",
        confidence: float = 0.85,
    ):
        self.document_type = document_type
        self.findings = findings or []
        self.summary = summary
        self.confidence = confidence

    def parse(self, text: str, fields: dict | None = None, pages: list | None = None):
        """Return a structured result matching AIParseResult."""
        from dataclasses import dataclass

        @dataclass
        class FakeResult:
            document_type: str
            confidence: float
            findings: list
            summary: str

        return FakeResult(
            document_type=self.document_type,
            confidence=self.confidence,
            findings=self.findings,
            summary=self.summary,
        )

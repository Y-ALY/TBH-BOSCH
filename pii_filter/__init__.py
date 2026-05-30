# pii_filter/__init__.py
"""
GDPR Fast Filtering Layer
==========================
A lightning-fast, regex-based first-pass scanner that flags documents
containing structured PII (emails, phones, IBANs, credit cards) before
heavier NLP / vector-DB stages run.

Public surface:
    - models         – Pydantic data-models for I/O
    - pii_scanner    – Core pattern-matching engine
    - state_manager  – SQLite-backed delta-scan tracker
    - pipeline       – High-level orchestrator (the main entry-point)
    - file_ingestor  – File-system reader (.pdf, .docx, .txt) → DocumentInput
"""

from pii_filter.models import (
    DocumentInput,
    PIIMatch,
    FlaggedDocument,
    PIIType,
)
from pii_filter.pipeline import FastFilterPipeline
from pii_filter.file_ingestor import ingest_directory, ingest_file

__all__ = [
    "DocumentInput",
    "PIIMatch",
    "FlaggedDocument",
    "PIIType",
    "FastFilterPipeline",
    "ingest_directory",
    "ingest_file",
]

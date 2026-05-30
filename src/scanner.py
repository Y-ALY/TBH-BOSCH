"""Full scan engine — orchestrates parse + classify + assign.

Single entry point: run_full_scan(connector) -> ScanResult.
"""

from __future__ import annotations

from .connector import Connector
from .models import ParsedDocument, ScanResult, Finding
from .pdf_parser import parse_pdf
from .classifier import extract_entities, classify_context
from .owner import assign_owners


# ---------------------------------------------------------------------------
# Field extraction — simple key:value line parser
# ---------------------------------------------------------------------------

_FIELD_PATTERNS = [
    # (regex, label)
    (r'(?im)^\s*(Company|Firma)\s*:\s*(.+)$', "Company"),
    (r'(?im)^\s*(Address|Adresse|Anschrift)\s*:\s*(.+)$', "Address"),
    (r'(?im)^\s*(Contact|Kontakt)\s*:\s*(.+)$', "Contact"),
    (r'(?im)^\s*(Tax ID|Steuer-ID|USt-IdNr\.?|VAT)\s*:\s*(.+)$', "Tax ID"),
    (r'(?im)^\s*(Employee|Mitarbeiter|Name)\s*:\s*(.+)$', "Employee"),
    (r'(?im)^\s*(Amount|Betrag|Summe|Total)\s*:\s*(.+)$', "Amount"),
    (r'(?im)^\s*(Date|Datum)\s*:\s*(.+)$', "Date"),
    (r'(?im)^\s*(Manager|Vorgesetzter)\s*:\s*(.+)$', "Manager"),
    (r'(?im)^\s*(System)\s*:\s*(.+)$', "System"),
    (r'(?im)^\s*(Access Level|Zugriffsstufe)\s*:\s*(.+)$', "Access Level"),
    (r'(?im)^\s*(Signature|Unterschrift)\s*:\s*(.+)$', "Signature"),
    (r'(?im)^\s*(Department|Abteilung)\s*:\s*(.+)$', "Department"),
    (r'(?im)^\s*(Email|E-Mail)\s*:\s*(.+)$', "Email"),
    (r'(?im)^\s*(Phone|Telefon|Tel\.?)\s*:\s*(.+)$', "Phone"),
    (r'(?im)^\s*(Participant|Teilnehmer)\s*:\s*(.+)$', "Participant"),
    (r'(?im)^\s*(Course|Kurs|Training)\s*:\s*(.+)$', "Course"),
    (r'(?im)^\s*(Score|Punktzahl|Rating|Bewertung)\s*:\s*(.+)$', "Score"),
]


def _extract_fields(text: str) -> dict[str, str]:
    """Extract labeled fields from document text using regex."""
    import re
    fields: dict[str, str] = {}
    for pattern, label in _FIELD_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = match.group(match.lastindex or 2).strip()
            if value and label not in fields:
                fields[label] = value
    return fields


# ---------------------------------------------------------------------------
# Full scan
# ---------------------------------------------------------------------------

def run_full_scan(connector: Connector) -> ScanResult:
    """Execute a complete scan across all files in the source.

    1. List all files via the connector.
    2. For each file: download → parse PDF → extract entities →
       classify document type → extract fields → assign owners.
    3. Return a ScanResult with all parsed documents and findings.
    """
    files = connector.list_files()
    scan_result = ScanResult(
        scan_id="",
        timestamp="",
        connector_type=type(connector).__name__,
        files_scanned=len(files),
        change_token=connector.get_change_token(),
    )

    for file_meta in files:
        # Download and hash
        raw_bytes = connector.download_file(file_meta.file_id)

        # Parse PDF
        pages, needs_ocr = parse_pdf(raw_bytes)
        full_text = "\n".join(p.text for p in pages)

        # Extract fields
        fields = _extract_fields(full_text) if not needs_ocr else {}

        # Classify document type
        doc_type = classify_context(full_text, fields) if not needs_ocr else "unknown"

        # Build parsed document
        parsed = ParsedDocument(
            file_id=file_meta.file_id,
            file_name=file_meta.file_name,
            source_type=file_meta.file_id.split(":")[0] if ":" in file_meta.file_id else "local",
            document_type=doc_type,
            page_count=len(pages),
            text_length=len(full_text),
            content_hash=file_meta.content_hash,
            owner_hints=connector.get_owner_hints(file_meta.file_id),
            needs_ocr=needs_ocr,
            pages=pages,
            fields=fields,
        )
        scan_result.parsed_documents.append(parsed)

        # Extract entities and assign owners (skip if OCR needed)
        if not needs_ocr:
            findings = extract_entities(full_text, pages)
            owner_hints = connector.get_owner_hints(file_meta.file_id)
            assign_owners(findings, owner_hints)

            # Set file_id on each finding
            for f in findings:
                f.file_id = file_meta.file_id

            scan_result.findings.extend(findings)

    return scan_result

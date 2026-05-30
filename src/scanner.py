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


# ---------------------------------------------------------------------------
# AI-enhanced scan (wraps full scan + AI enrichment)
# ---------------------------------------------------------------------------

def run_ai_scan(connector, ai_parser=None) -> ScanResult:
    """Run full scan, then enrich with AI parsing on each document.

    AI overrides:
      - document_type (if confidence > 0.6)
      - adds AI-only findings regex can't catch
      - upgrades/downgrades risk on existing findings based on context

    Falls back gracefully to regex-only if AI parser is unavailable.
    """
    result = run_full_scan(connector)

    if ai_parser is None:
        return result  # regex-only mode

    for doc in result.parsed_documents:
        if doc.needs_ocr:
            continue

        full_text = "\n".join(p.text for p in doc.pages)
        regex_count = len([f for f in result.findings if f.file_id == doc.file_id])

        # ── Gatekeeper: skip AI for confidently classified, clean docs ──
        if doc.document_type != "unknown" and regex_count == 0:
            continue

        # ── AI analysis ────────────────────────────────────────
        try:
            ai_result = ai_parser.parse(
                text=full_text,
                fields=doc.fields,
                page_count=doc.page_count,
                regex_findings_count=regex_count,
            )

            # Override document type if AI is confident
            if ai_result.confidence > 0.6 and ai_result.document_type != "unknown":
                doc.document_type = ai_result.document_type

            # Merge AI findings — convert to our Finding model
            for af in ai_result.findings:
                f = Finding(
                    finding_id="",
                    file_id=doc.file_id,
                    type=af.get("type", "other_pii"),
                    value=str(af.get("value", "")),
                    field=af.get("field", ""),
                    context=af.get("context", ""),
                    risk_level=af.get("risk_level", "medium"),
                    confidence=float(af.get("confidence", 0.8)),
                    evidence=f"AI ({ai_result.model_used}): {af.get('context', '')[:100]}",
                    recommended_action=af.get("recommended_action", "review"),
                )
                result.findings.append(f)

            # Store AI metadata on the document
            doc.fields["_ai_model"] = ai_result.model_used
            doc.fields["_ai_tokens"] = str(ai_result.tokens_used)
            doc.fields["_ai_confidence"] = str(ai_result.confidence)

        except Exception as e:
            doc.fields["_ai_error"] = str(e)

    # Re-assign owners (AI may have added findings)
    for doc in result.parsed_documents:
        doc_findings = [f for f in result.findings if f.file_id == doc.file_id]
        owner_hints = connector.get_owner_hints(doc.file_id)
        assign_owners(doc_findings, owner_hints)

    return result

"""Full scan engine — orchestrates parse + classify + assign.

Single entry point: run_full_scan(connector) -> ScanResult.
"""

from __future__ import annotations

from .connector import Connector
from .models import ParsedDocument, ScanResult, Finding
from .pdf_parser import parse_pdf
from .classifier import extract_entities, classify_context
from .owner import assign_owners
import re


# ---------------------------------------------------------------------------
# Field extraction — simple key:value line parser
# ---------------------------------------------------------------------------

_FIELD_PATTERNS = [
    # (compiled_regex, label)
    (re.compile(r'(?im)^\s*(Company|Firma)\s*:\s*(.+)$'), "Company"),
    (re.compile(r'(?im)^\s*(Address|Adresse|Anschrift)\s*:\s*(.+)$'), "Address"),
    (re.compile(r'(?im)^\s*(Contact|Kontakt)\s*:\s*(.+)$'), "Contact"),
    (re.compile(r'(?im)^\s*(Tax ID|Steuer-ID|USt-IdNr\.?|VAT)\s*:\s*(.+)$'), "Tax ID"),
    (re.compile(r'(?im)^\s*(Employee|Mitarbeiter|Name)\s*:\s*(.+)$'), "Employee"),
    (re.compile(r'(?im)^\s*(Amount|Betrag|Summe|Total)\s*:\s*(.+)$'), "Amount"),
    (re.compile(r'(?im)^\s*(Date|Datum)\s*:\s*(.+)$'), "Date"),
    (re.compile(r'(?im)^\s*(Manager|Vorgesetzter)\s*:\s*(.+)$'), "Manager"),
    (re.compile(r'(?im)^\s*(System)\s*:\s*(.+)$'), "System"),
    (re.compile(r'(?im)^\s*(Access Level|Zugriffsstufe)\s*:\s*(.+)$'), "Access Level"),
    (re.compile(r'(?im)^\s*(Signature|Unterschrift)\s*:\s*(.+)$'), "Signature"),
    (re.compile(r'(?im)^\s*(Department|Abteilung)\s*:\s*(.+)$'), "Department"),
    (re.compile(r'(?im)^\s*(Email|E-Mail)\s*:\s*(.+)$'), "Email"),
    (re.compile(r'(?im)^\s*(Phone|Telefon|Tel\.?)\s*:\s*(.+)$'), "Phone"),
    (re.compile(r'(?im)^\s*(Participant|Teilnehmer)\s*:\s*(.+)$'), "Participant"),
    (re.compile(r'(?im)^\s*(Course|Kurs|Training)\s*:\s*(.+)$'), "Course"),
    (re.compile(r'(?im)^\s*(Score|Punktzahl|Rating|Bewertung)\s*:\s*(.+)$'), "Score"),
]


def _extract_fields(text: str) -> dict[str, str]:
    """Extract labeled fields from document text using regex."""
    fields: dict[str, str] = {}
    for pattern, label in _FIELD_PATTERNS:
        match = pattern.search(text)
        if match:
            value = match.group(match.lastindex or 2).strip()
            if value and label not in fields:
                fields[label] = value
    return fields


# ---------------------------------------------------------------------------
# Full scan
# ---------------------------------------------------------------------------

def run_full_scan(connector: Connector, *, db_session=None) -> ScanResult:
    """Execute a complete scan across all files in the source.

    1. List all files via the connector.
    2. For each file: download → parse PDF → extract entities →
       classify document type → extract fields → assign owners.
    3. Return a ScanResult with all parsed documents and findings.

    Args:
        connector:   Data source connector.
        db_session:  Optional SQLAlchemy session for dynamic Employee lookups.
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
            assign_owners(
                findings,
                owner_hints,
                file_path=file_meta.path,
                fields=fields,
                db_session=db_session,
            )

            # Set file_id on each finding
            for f in findings:
                f.file_id = file_meta.file_id

            scan_result.findings.extend(findings)

    return scan_result


# ---------------------------------------------------------------------------
# AI-enhanced scan (wraps full scan + AI enrichment)
# ---------------------------------------------------------------------------

def run_ai_scan(connector, ai_parser=None, *, db_session=None) -> ScanResult:
    """Run full scan, then enrich with AI parsing on each document.

    AI overrides:
      - document_type (if confidence > 0.6)
      - adds AI-only findings regex can't catch
      - upgrades/downgrades risk on existing findings based on context

    Falls back gracefully to regex-only if AI parser is unavailable.

    Args:
        connector:   Data source connector.
        ai_parser:   Optional AI parser instance.
        db_session:  Optional SQLAlchemy session for dynamic Employee lookups.
    """
    result = run_full_scan(connector, db_session=db_session)

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
    # Resolve file path for each document for path-based owner detection
    for doc in result.parsed_documents:
        doc_findings = [f for f in result.findings if f.file_id == doc.file_id]
        owner_hints = connector.get_owner_hints(doc.file_id)
        # Resolve the physical file path from the connector
        file_meta = connector.get_file_metadata(doc.file_id)
        file_path = file_meta.path if file_meta else ""
        assign_owners(
            doc_findings,
            owner_hints,
            file_path=file_path,
            fields=doc.fields,
            db_session=db_session,
        )

    return result


# ---------------------------------------------------------------------------
# Layered scan — streaming regex scan + async AI enrichment
# ---------------------------------------------------------------------------

def run_layered_scan(
    connector,
    file_refs,
    options: "ScanOptions | None" = None,
    ai_parser=None,
    *,
    db_session=None,
    on_result=None,
    on_error=None,
) -> "tuple[ScanMetrics, object]":
    """Streaming scan with async AI enrichment.

    1. Run streaming scan (regex only) via run_streaming_scan()
    2. For each FileScanResult, pass through AIGate
    3. If gate says yes -> enqueue in AIQueue
    4. Return ScanMetrics immediately (don't wait for AI)
    5. AIQueue workers process in background

    Returns:
        (ScanMetrics, AIQueue) — metrics available immediately,
        AI results accessible via ai_queue.get_results(file_id)
    """
    from .models import ScanOptions as _ScanOptions
    from .models import ScanMetrics
    from .streaming_scanner import run_streaming_scan
    from .ai_queue import AIGate, AIQueue, AIQueueItem

    if options is None:
        options = _ScanOptions()

    # Build AI queue (graceful fallback if no parser)
    gate = AIGate()
    ai_queue = AIQueue(ai_parser=ai_parser)

    # Start background workers immediately
    ai_queue.start()

    def _on_result(file_result) -> None:
        """Streaming scan callback: gate check + enqueue."""
        # Forward to caller's callback if provided
        if callable(on_result):
            on_result(file_result)

        # Gate check
        if not gate.should_enrich(file_result, options):
            return

        # Build queue item and enqueue
        risk_levels = []
        for f in file_result.findings:
            if hasattr(f, "risk_level"):
                risk_levels.append(f.risk_level)

        item = AIQueueItem(
            scan_id="",  # filled by metrics or caller
            file_id=file_result.file_ref.file_id,
            document_type=file_result.document_type,
            regex_findings_count=len(file_result.findings),
            risk_levels=risk_levels,
            text=file_result.text,
            fields=dict(file_result.fields),
            page_count=file_result.page_count,
        )
        ai_queue.enqueue(item)

    # Run streaming scan with AI off (AI happens async in background)
    metrics = run_streaming_scan(
        connector,
        file_refs,
        _ScanOptions(mode=options.mode, ai_mode="off", max_workers=options.max_workers),
        db_session=db_session,
        on_result=_on_result,
        on_error=on_error,
    )

    return metrics, ai_queue

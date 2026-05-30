"""CLI entry point for the GDPR scanning pipeline.

Subcommands:
    full-scan       Run a complete scan across all files.
    delta-scan      Compare current state against a previous baseline.
    review          Process a human review action.
    export-review   Export the review queue as JSON.
    init-demo       Create demo directory structure and template files.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .connector import LocalSampleRepoConnector
from .scanner import run_full_scan, run_ai_scan
from .review import create_review_queue, process_review_action, export_review_queue
from .delta import save_state, compare_delta


# ---------------------------------------------------------------------------
# full-scan
# ---------------------------------------------------------------------------

def cmd_full_scan(args: argparse.Namespace) -> int:
    connector = LocalSampleRepoConnector(args.repo, args.owner_hints)
    result = run_full_scan(connector)

    # Save scan result
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    scan_path = out_dir / f"scan_{result.scan_id}.json"
    _write_json(scan_path, _scan_result_to_dict(result))
    print(f"Scan result → {scan_path}")

    # Save delta state
    state_dir = Path(args.output).parent / "state"
    state_path = save_state(result, str(state_dir))
    print(f"Delta state  → {state_path}")

    # Summary
    print(f"\n{'='*60}")
    print(f"Scan ID:       {result.scan_id}")
    print(f"Files scanned: {result.files_scanned}")
    print(f"Documents:     {len(result.parsed_documents)}")
    print(f"Findings:      {len(result.findings)}")
    print(f"Change token:  {result.change_token[:16]}...")

    # Breakdown
    doc_types: dict[str, int] = {}
    for doc in result.parsed_documents:
        doc_types[doc.document_type] = doc_types.get(doc.document_type, 0) + 1
    print(f"\nDocument types:")
    for dt, count in sorted(doc_types.items()):
        print(f"  {dt}: {count}")

    finding_types: dict[str, int] = {}
    for f in result.findings:
        finding_types[f.type] = finding_types.get(f.type, 0) + 1
    print(f"\nFinding types:")
    for ft, count in sorted(finding_types.items()):
        print(f"  {ft}: {count}")

    unresolved = sum(1 for f in result.findings if not f.owner_resolved)
    if unresolved:
        print(f"\n⚠  {unresolved} finding(s) with unresolved owner → escalated to DPO")

    return 0


# ---------------------------------------------------------------------------
# ai-scan
# ---------------------------------------------------------------------------

def cmd_ai_scan(args: argparse.Namespace) -> int:
    from .ai_parser import AIParser

    connector = LocalSampleRepoConnector(args.repo, args.owner_hints)

    # Try to init AI parser
    try:
        ai = AIParser(model=args.model)
        print(f"🤖 AI parser: {ai.model}")
    except (ValueError, ImportError) as e:
        print(f"⚠️  AI parser unavailable: {e}")
        print("   Falling back to regex-only mode.")
        ai = None

    result = run_ai_scan(connector, ai)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    scan_path = out_dir / f"scan_{result.scan_id}.json"
    _write_json(scan_path, _scan_result_to_dict(result))
    print(f"Scan result → {scan_path}")

    # Count AI vs regex findings
    ai_count = sum(1 for f in result.findings if "AI" in f.evidence)
    regex_count = len(result.findings) - ai_count

    print(f"\n{'='*60}")
    print(f"Scan ID:       {result.scan_id}")
    print(f"Files scanned: {result.files_scanned}")
    print(f"Findings:      {len(result.findings)} ({regex_count} regex + {ai_count} AI)")
    print(f"Change token:  {result.change_token[:16]}...")

    finding_types: dict[str, int] = {}
    for f in result.findings:
        finding_types[f.type] = finding_types.get(f.type, 0) + 1
    print(f"\nFinding types:")
    for ft, count in sorted(finding_types.items()):
        print(f"  {ft}: {count}")

    return 0


# ---------------------------------------------------------------------------
# delta-scan
# ---------------------------------------------------------------------------

def cmd_delta_scan(args: argparse.Namespace) -> int:
    connector = LocalSampleRepoConnector(args.repo, args.owner_hints)
    report = compare_delta(connector, args.previous_state)

    # Save report
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"delta_{report.scan_id}.json"
    _write_json(report_path, _delta_report_to_dict(report))
    print(f"Delta report → {report_path}")

    # Summary
    print(f"\n{'='*60}")
    print(f"Previous scan:  {report.previous_scan_id}")
    print(f"Added:          {len(report.added)}")
    print(f"Modified:       {len(report.modified)}")
    print(f"Removed:        {len(report.removed)}")
    print(f"Unchanged:      {report.unchanged}")
    print(f"Needs full scan: {report.needs_full_scan}")

    for f in report.added:
        print(f"  + {f.file_name}")
    for f in report.modified:
        print(f"  ~ {f.file_name}")
    for name in report.removed:
        print(f"  - {name}")

    return 0


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

def cmd_review(args: argparse.Namespace) -> int:
    # Load findings from a scan result
    with open(args.findings) as f:
        data = json.load(f)

    # Build review queue from findings
    from .models import Finding
    findings = [_dict_to_finding(fd) for fd in data.get("findings", [])]
    review_items = create_review_queue(findings)

    # Process the action
    result = process_review_action(
        review_items, args.review_id, args.action, args.reviewer, args.reason
    )

    if isinstance(result, str):
        print(f"Error: {result}", file=sys.stderr)
        return 1

    # Save updated queue
    out_dir = Path(args.output) if args.output else Path("data/output")
    out_dir.mkdir(parents=True, exist_ok=True)
    export_review_queue(review_items, str(out_dir / "review_queue.json"))

    print(f"Review {args.review_id}: {args.action} by {args.reviewer}")
    print(f"Reason: {args.reason}")
    print(f"Updated queue → {out_dir / 'review_queue.json'}")
    return 0


# ---------------------------------------------------------------------------
# export-review
# ---------------------------------------------------------------------------

def cmd_export_review(args: argparse.Namespace) -> int:
    with open(args.findings) as f:
        data = json.load(f)

    from .models import Finding
    findings = [_dict_to_finding(fd) for fd in data.get("findings", [])]
    review_items = create_review_queue(findings)

    out_path = export_review_queue(review_items, args.output)
    print(f"Review queue exported → {out_path}")
    print(f"  Total:     {len(review_items)}")
    print(f"  Pending:   {sum(1 for r in review_items if r.status == 'pending')}")
    return 0


# ---------------------------------------------------------------------------
# init-demo
# ---------------------------------------------------------------------------

def cmd_init_demo(args: argparse.Namespace) -> int:
    base = Path(args.dir)
    pdfs_dir = base / "sample_pdfs"
    output_dir = base / "output"
    state_dir = base / "state"

    pdfs_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write owner hints template
    hints_path = base / "owner_hints.json"
    if not hints_path.exists():
        _write_json(hints_path, {
            "_comment": "Map file names to owner information. Used by LocalSampleRepoConnector.",
            "example.pdf": {
                "name": "Anna Schmidt",
                "email": "anna.schmidt@bosch.com",
                "department": "Finance",
                "site_owner": "Dr. Mueller",
                "master_of_data": "CDO Office",
            },
        })
        print(f"Owner hints template → {hints_path}")

    # Write README
    readme_path = pdfs_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(DEMO_README)
        print(f"README → {readme_path}")

    print(f"\nDemo structure created under {base}/")
    print(f"  sample_pdfs/  ← drop your 15 PDFs here")
    print(f"  output/       ← scan results land here")
    print(f"  state/        ← delta state snapshots")
    print(f"  owner_hints.json ← edit to add owner info")
    print(f"\nNext: python -m src.pipeline full-scan --repo {base}/sample_pdfs --owner-hints {base}/owner_hints.json --output {base}/output/")
    return 0


DEMO_README = """# Sample PDFs for GDPR Demo

Drop your 15 demo PDFs here. Expected types:

| File | Expected Type |
|------|--------------|
| Supplier onboarding forms | supplier_onboarding |
| Expense reports | expense_report |
| IT access request forms | it_access_request |
| Incident reports | incident_report |
| Training evaluations | training_evaluation |

## Parsing Expectations

- **Supplier** files: Company, Address, Contact, Tax ID
- **Expense** files: Employee, Amount, Date, Manager
- **IT Access** files: Name, System, Access Level, Signature

## Owner Hints

Edit `../owner_hints.json` to map files to owners.
Files without owner hints will be escalated to DPO.
"""


# ---------------------------------------------------------------------------
# CLI setup
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.pipeline",
        description="GDPR Data Governance Scanning Pipeline",
    )
    sub = parser.add_subparsers(dest="command")

    # full-scan
    p_full = sub.add_parser("full-scan", help="Run a complete scan across all files")
    p_full.add_argument("--repo", required=True, help="Path to PDF directory")
    p_full.add_argument("--owner-hints", default=None, help="Path to owner hints JSON")
    p_full.add_argument("--output", default="data/output", help="Output directory for results")
    p_full.set_defaults(func=cmd_full_scan)

    # ai-scan
    p_ai = sub.add_parser("ai-scan", help="Run full scan with AI enrichment (needs OPENROUTER_API_KEY)")
    p_ai.add_argument("--repo", required=True, help="Path to PDF directory")
    p_ai.add_argument("--owner-hints", default=None, help="Path to owner hints JSON")
    p_ai.add_argument("--output", default="data/output", help="Output directory for results")
    p_ai.add_argument("--model", default=None, help="OpenRouter model (default: anthropic/claude-sonnet-4)")
    p_ai.set_defaults(func=cmd_ai_scan)

    # delta-scan
    p_delta = sub.add_parser("delta-scan", help="Compare current state against previous baseline")
    p_delta.add_argument("--repo", required=True, help="Path to PDF directory")
    p_delta.add_argument("--owner-hints", default=None, help="Path to owner hints JSON")
    p_delta.add_argument("--previous-state", required=True, help="Path to previous delta state JSON")
    p_delta.add_argument("--output", default="data/output", help="Output directory for delta report")
    p_delta.set_defaults(func=cmd_delta_scan)

    # review
    p_rev = sub.add_parser("review", help="Process a human review action")
    p_rev.add_argument("--findings", required=True, help="Path to scan result JSON containing findings")
    p_rev.add_argument("--review-id", required=True, help="Review item ID to act on")
    p_rev.add_argument("--action", required=True, help="Action: retain|delete|archive|mask|false_positive|escalate_dpo")
    p_rev.add_argument("--reviewer", required=True, help="Name of reviewer")
    p_rev.add_argument("--reason", required=True, help="Reason for the decision")
    p_rev.add_argument("--output", default="data/output", help="Output directory for updated queue")
    p_rev.set_defaults(func=cmd_review)

    # export-review
    p_exp = sub.add_parser("export-review", help="Export the review queue as JSON")
    p_exp.add_argument("--findings", required=True, help="Path to scan result JSON containing findings")
    p_exp.add_argument("--output", default="data/output/review_queue.json", help="Output path")
    p_exp.set_defaults(func=cmd_export_review)

    # init-demo
    p_init = sub.add_parser("init-demo", help="Create demo directory structure and template files")
    p_init.add_argument("--dir", default="data", help="Base data directory (default: data/)")
    p_init.set_defaults(func=cmd_init_demo)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    sys.exit(args.func(args) or 0)


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------

def _scan_result_to_dict(result) -> dict:
    return {
        "scan_id": result.scan_id,
        "timestamp": result.timestamp,
        "connector_type": result.connector_type,
        "files_scanned": result.files_scanned,
        "change_token": result.change_token,
        "parsed_documents": [
            {
                "file_id": d.file_id,
                "file_name": d.file_name,
                "source_type": d.source_type,
                "document_type": d.document_type,
                "page_count": d.page_count,
                "text_length": d.text_length,
                "content_hash": d.content_hash,
                "owner_hints": d.owner_hints,
                "needs_ocr": d.needs_ocr,
                "pages": [{"page_number": p.page_number, "text": p.text, "char_count": p.char_count} for p in d.pages],
                "fields": d.fields,
            }
            for d in result.parsed_documents
        ],
        "findings": [
            {
                "finding_id": f.finding_id,
                "file_id": f.file_id,
                "type": f.type,
                "value": f.value,
                "field": f.field,
                "context": f.context,
                "risk_level": f.risk_level,
                "confidence": f.confidence,
                "evidence": f.evidence,
                "recommended_action": f.recommended_action,
                "assigned_owner": f.assigned_owner,
                "owner_email": f.owner_email,
                "owner_department": f.owner_department,
                "owner_resolved": f.owner_resolved,
                "escalation_target": f.escalation_target,
            }
            for f in result.findings
        ],
    }


def _delta_report_to_dict(report) -> dict:
    return {
        "scan_id": report.scan_id,
        "previous_scan_id": report.previous_scan_id,
        "timestamp": report.timestamp,
        "added": [_file_meta_to_dict(f) for f in report.added],
        "modified": [_file_meta_to_dict(f) for f in report.modified],
        "removed": report.removed,
        "unchanged": report.unchanged,
        "missing": report.missing,
        "needs_full_scan": report.needs_full_scan,
    }


def _file_meta_to_dict(f) -> dict:
    return {
        "file_id": f.file_id,
        "file_name": f.file_name,
        "path": f.path,
        "size_bytes": f.size_bytes,
        "last_modified": f.last_modified,
        "content_hash": f.content_hash,
        "mime_type": f.mime_type,
    }


def _dict_to_finding(d: dict) -> "Finding":
    from .models import Finding
    return Finding(
        finding_id=d.get("finding_id", ""),
        file_id=d.get("file_id", ""),
        type=d.get("type", ""),
        value=d.get("value", ""),
        field=d.get("field", ""),
        context=d.get("context", "unknown"),
        risk_level=d.get("risk_level", "medium"),
        confidence=d.get("confidence", 1.0),
        evidence=d.get("evidence", ""),
        recommended_action=d.get("recommended_action", "review"),
        assigned_owner=d.get("assigned_owner", ""),
        owner_email=d.get("owner_email", ""),
        owner_department=d.get("owner_department", ""),
        owner_resolved=d.get("owner_resolved", False),
        escalation_target=d.get("escalation_target", ""),
    )


def _write_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()

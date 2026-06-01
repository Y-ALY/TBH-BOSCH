# Bosch GDPR Data Discovery Prototype

TechON Hackathon 2026 prototype for automated discovery, classification, and human review of GDPR-relevant personal data across corporate file repositories.

## Objective

Large organizations such as Bosch hold personal data across OneDrive, SharePoint, shared drives, and other distributed repositories. Manual review does not scale across hundreds of thousands of locations, so this prototype demonstrates an automated discovery pipeline that:

- scans unstructured documents for personal and sensitive data,
- classifies findings by GDPR-relevant category and risk,
- assigns each finding to a responsible data owner,
- supports full scans and repeat delta scans,
- surfaces findings to employees and administrators for human review,
- applies a 3-year retention policy when calculating deletion deadlines.

The system is a proof of concept. It recommends actions and prepares evidence, but deletion or retention decisions remain human-controlled.

## Hackathon Scope Mapping

| Priority | Requirement | Prototype implementation |
| --- | --- | --- |
| 1 | Scan logic | `src/scanner.py`, `src/classifier.py`, `src/pdf_parser.py`, and `src/ai_parser.py` perform PDF parsing, regex detection, optional AI enrichment, risk classification, and owner assignment. |
| 2 | Employee frontend | `/employee-dashboard` shows only the logged-in employee's responsible files, flagged snippets, retention status, and guided actions such as delete expired data or extend retention. |
| 3 | Admin frontend | `/admin-dashboard` and `/admin-database-explorer` expose KPIs including scanned files, scanned data volume, flagged files, expiring files, and employee-level drilldown. |
| 4 | Data connectors | The local connector is demo-ready. Microsoft Graph, Google Drive, and connector abstractions exist for future OneDrive, SharePoint, and drive integration. |

## Detection Coverage

The deterministic scanner currently detects common GDPR-relevant entities including:

- names in labeled fields,
- usernames and employee IDs,
- email addresses,
- phone numbers,
- home or business addresses,
- signatures,
- passport numbers,
- ID card numbers,
- driver's license numbers,
- IBANs,
- tax IDs,
- dates of birth,
- contextual secrets such as passwords.

The optional AI parser can enrich document classification and add contextual findings that regex alone may miss.

## Architecture

```text
Data source connector
  -> file discovery and metadata
  -> delta comparison
  -> document parsing / OCR-ready extraction
  -> deterministic PII scan
  -> optional AI enrichment
  -> owner assignment
  -> SQLite persistence
  -> employee/admin dashboards
```

Important files:

- `main.py` - FastAPI web dashboard and demo routes.
- `api.py` - standalone scan API for triggering scans and reviewing findings.
- `database.py` - SQLite schema for employees, files, findings, scan jobs, and notifications.
- `src/scanner.py` - core full and AI-enhanced scan orchestration.
- `src/classifier.py` - deterministic PII patterns and document classification.
- `src/delta.py` - repeat-scan state and change detection.
- `src/streaming_scanner.py` - scalable streaming scan path.
- `docs/adr-001-scan-architecture.md` - architecture decision record for the optimized scan design.

## Run The Demo

Install dependencies, then start the dashboard:

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

Demo login:

- Admin: `admin@bosch.com` / `password123`
- Employees: generated from `demo_drive_rich/owner_hints.json`, also using `password123`

Trigger a scan from the admin UI or call:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/trigger-scan \
  -H "Content-Type: application/json" \
  -H "Cookie: session_emp_id=BX-ADMIN" \
  -d '{"target_dir":"./demo_drive_rich"}'
```

## Admin Scan Workflow

The admin dashboard now follows the intended product flow:

1. Log in as `admin@bosch.com` / `password123`.
2. Open **Scan Intake** on `/admin-dashboard`.
3. Either upload one or more files, or paste a local folder/file path, `file://` URL, or direct downloadable `https://` file URL.
4. The scanner analyzes the source, writes files and findings into SQLite, refreshes KPIs, and links to `/data-points` for review.

The same workflow is available through API endpoints:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/intake/link \
  -H "Content-Type: application/json" \
  -H "Cookie: session_emp_id=BX-ADMIN" \
  -d '{"source":"./sample_docs"}'
```

```bash
curl -X POST http://127.0.0.1:8000/api/admin/intake/upload \
  -H "Cookie: session_emp_id=BX-ADMIN" \
  -F "files=@./sample_docs/test_memo.txt"
```

The extraction path includes a regression guard for the current performance target: a 1 MB text source must scan in under 5 seconds.

## Evaluation Criteria

The prototype is designed around the official evaluation dimensions:

- **Accuracy:** deterministic regex rules for reproducible baseline detection, with optional AI enrichment for context-sensitive cases.
- **Reproducibility:** repeated regex-only scans produce stable results for the same input files.
- **Speed:** delta scanning avoids reprocessing unchanged files; streaming scan logic supports bounded-memory processing.
- **Resource intensity:** metadata-first delta planning and layered AI reduce downloads, memory usage, and API calls.

## Retention Policy

The official retention period is 3 years. The application calculates a file's retention deadline as:

```text
retention_deadline = last_modified + 3 years
```

Expired files are shown to the responsible employee and can be cleaned up through the dashboard. Retention extension remains a guided, auditable human action.

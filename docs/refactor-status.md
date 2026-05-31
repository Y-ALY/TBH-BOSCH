# Refactor Status

> Owned by Agent 0 (Coordinator). Tracks progress of all agents in Phase 1.

## Phase 1 Complete (2026-06-01)

### Git
- Branch: `major-restructure` (pushed to origin, worktree `refactor-coordinator`)
- Commits: 9 incremental commits covering all agent work

### Final Test Suite
- **294 passed**, **3 failed** (pre-existing, categorized), **2 skipped**, 8 warnings
- 73 new tests added (299 total vs 226 original)
- Failures (all in `tests/test_classifier.py` — see Agent 2 section for categorization):
  1. `test_classifier_matches_regex_scanner_contextual_pii` — test expectation needs update
  2. `test_classifier_semantic_phone_still_detects_contextual_phone` — test expectation needs update
  3. `test_classifier_blank_templates_are_not_flagged_as_contextual_pii` — genuine bug
- Root-level `test_script.py` has `exit()` — now excluded via `pyproject.toml`
- Python 3.9.6, key packages: fastapi 0.128.8, uvicorn 0.39.0, SQLAlchemy 2.0.50, pytest 8.4.2

### App Startup
- Not yet smoke-tested

### Root Directory Clutter (preliminary)
See Agent 7 for full classification. Notable:
- `fix_main.py`, `fix_quotes.py`, `fix_zombies.py`, `repair.py`, `replace_emojis.py`
- `clean_script.js`, `clean_script2.js`, `temp_script.js`, `test_script2.js`
- `gemini-code-1780144486142.py`, `gemini-code-1780145060717.py`
- `diff.txt`, `gdpr_loaders.html`, `gdpr_modal.html`, `html_guide.md`
- `classified_results.json`, `scan_results.json`
- `test_script.py` (has exit())
- `test_inject.py`, `test_audit_file.py`, `test_ai_gatekeeper.py`, `test_pdf_pipeline.py` (root-level tests)
- `generate_gdpr_pdf_test_set.py`, `seed_json_data.py`, `seed_test_expired.py`, `demo.py`, `demo_ingest.py`
- `uvicorn.log`

---

## Agent Status

| Agent | Status | Key Deliverables |
|-------|--------|------------------|
| 0 - Coordinator | ✅ complete | Baseline, plan, final integration |
| 1 - Architecture Map | ✅ complete | `docs/architecture-map.md` (568 lines) |
| 2 - Test Baseline | ✅ complete | conftest.py, 73 new tests, pyproject.toml |
| 3 - API Boundary | ✅ complete | `app/routes/scan.py`, `app/startup.py` |
| 4 - Scan Engine | ✅ complete | `src/scan_service.py`, entry point docs |
| 5 - DB Hardening | ✅ complete | Sectioned database.py, 15 DB writer tests |
| 6 - Production Readiness | ✅ complete | `docs/production-readiness.md` (453 lines) |
| 7 - Repo Hygiene | ✅ complete | `docs/root-cleanup-proposal.md`, `.gitignore` |
| 8 - UI Route Stabilization | ✅ complete | `docs/ui-route-map.md`, smoke checklist |
| 15 - Deployment | ✅ complete | `render.yaml`, PostgreSQL, seed script, runbook |

## Execution Summary

```
✅ 0 → ✅ 1 → ✅ 2 → ✅ 6 → ✅ 7 → ✅ 15
→ ✅ 4 → ✅ 5 → ✅ 3 → ✅ 8 → ✅ 0 final
```

---

## Agent 2: Test Baseline and Safety Net (completed 2026-06-01)

### Test Results

- **283 passed**, **3 failed** (pre-existing, unchanged), **2 skipped**, 6 warnings
- 71 new tests added (288 total vs 217 original)
- No regressions in existing tests

### Pre-existing Failures (categorized, NOT fixed)

All 3 in `tests/test_classifier.py`:

1. **`test_classifier_matches_regex_scanner_contextual_pii`** — **Test expectation needs update**. The name regex pattern captures the full remainder of the labeled line, so `"Employee: Sara Hoffmann (E-20491)"` produces value `"Sara Hoffmann (E-20491)"`, not `"Sara Hoffmann"`. The classifier is working as designed; the test expectation is too narrow.

2. **`test_classifier_semantic_phone_still_detects_contextual_phone`** — **Test expectation needs update**. The phone number `+49 170 1234567` IS detected, but in Pass 1 (Regex_Match) rather than Pass 2 (Semantic_Match). No product behavior change — the phone is still found. The test's flag_type assertion needs to accept `Regex_Match` instead of requiring `Semantic_Match`.

3. **`test_classifier_blank_templates_are_not_flagged_as_contextual_pii`** — **Genuine bug (not fixed per scope)**. Blank template placeholders like `"Signature: ______"` and `"Name: ______"` are incorrectly flagged as PII. The classifier lacks logic to skip placeholder values. This is a genuine false-positive bug that should be fixed in a separate change (not in Agent 2's scope).

### New Test Files Created

- **`tests/conftest.py`** — Shared fixtures: in-memory SQLite engine (StaticPool), `db_session` (SAVEPOINT isolation), `main_test_client`, `api_test_client`, `sample_pdf_dir` (minimal hand-crafted PDFs), `FakeAIParser` (deterministic, zero API calls)
- **`tests/test_core_scan_chain.py`** — 38 tests covering:
  - Connector: list_files, iter_files, download_file, open_file, get_change_token, empty/nonexistent paths, abstract base class
  - PDF parser: sample doc parsing, page structure, minimal PDF handling
  - Classifier: email, phone, tax_id, employee_id, name, address, IBAN detection; document type classification (all 5 types + unknown); external findings merge; clean text produces no findings
  - Extractor: scan_file structure, scan_directory aggregates, owner hints, nonexistent path
  - Owner assignment: static hints (with/without DB), path-based extraction, fallback to site_owner/master_of_data/DPO, empty findings noop
  - DB writer: write findings, upsert existing, write file state, flush counting, pending count
  - Review actions: allowed list, reject invalid, accept all valid
  - Scan job model: default status on flush, update to completed
- **`tests/test_api_smoke.py`** — 21 tests covering:
  - main.py: GET / (login page), POST /login (invalid + valid), admin/employee dashboard redirects, KPI auth denial, search auth denial
  - api.py: GET /api/health, GET /api/scans (empty), GET /api/findings (empty + filtered), POST /api/scan (nonexistent folder + valid folder), GET /api/scan/{id} (not found)
  - Review: invalid action (422), nonexistent finding (404), valid mask + delete actions with DB verification
  - FakeAIParser: deterministic results, no API key required
- **`pyproject.toml`** — Excludes root-level `test_script.py` from pytest collection

### Risks / Follow-ups

1. The 3 pre-existing classifier failures should be addressed. Failure #1 and #2 are test expectation updates (low risk). Failure #3 is a genuine false-positive bug that should be fixed with a placeholder-value filter.
2. Root-level `test_ai_gatekeeper.py` and `test_audit_file.py` are collected when running `pytest` from root. They don't have exit() but are in the wrong location.
3. The hand-crafted PDF files use Helvetica font with BT/ET text operators — text extraction via pdfplumber may be unreliable for these minimal PDFs. Tests that need guaranteed text extraction should use real PDFs from `sample_docs/`.
4. The `db_session` fixture uses SAVEPOINT isolation via StaticPool. Tests that use both `db_session` and a TestClient fixture in the same function will conflict. The API smoke tests avoid this by creating standalone sessions directly from the engine.


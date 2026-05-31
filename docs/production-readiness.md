# Production Readiness Audit

**Date:** 2026-06-01
**Author:** Agent 6 (Security and Compliance Reality Check)
**Status:** Living document for stakeholder review

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Risk Summary (Dashboard)](#2-risk-summary-dashboard)
3. [Detailed Findings](#3-detailed-findings)
   - 3.1 [Secrets Exposed in Repository](#31-secrets-exposed-in-repository)
   - 3.2 [Plaintext Password Storage](#32-plaintext-password-storage)
   - 3.3 [Wildcard CORS Configuration](#33-wildcard-cors-configuration)
   - 3.4 [SQLite Limitations for Multi-User/Worker](#34-sqlite-limitations-for-multi-userworker)
   - 3.5 [Unrestricted Filesystem Path Scanning](#35-unrestricted-filesystem-path-scanning)
   - 3.6 [Missing Authorization on Admin Endpoints](#36-missing-authorization-on-admin-endpoints)
   - 3.7 [Missing Audit Trail](#37-missing-audit-trail)
   - 3.8 [AI Data Sharing with OpenRouter](#38-ai-data-sharing-with-openrouter)
   - 3.9 [Logging of Sensitive Data](#39-logging-of-sensitive-data)
   - 3.10 [File Serving Without Path Sanitization](#310-file-serving-without-path-sanitization)
   - 3.11 [Database Hardcoded to SQLite](#311-database-hardcoded-to-sqlite)
4. [What Is Already Safe](#4-what-is-already-safe)
5. [Remediation Roadmap](#5-remediation-roadmap)
6. [Summary of This Pass](#6-summary-of-this-pass)

---

## 1. Purpose

This document tells stakeholders **what is demo-quality vs production-ready** in the TBH-BOSCH GDPR scanner. Each finding includes severity, a plain-language description, the concrete impact, and a proposed remediation path.

**Scope of this audit:** The current codebase on `main` branch as of 2026-06-01. This does not yet cover external dependencies, container images, or network-level concerns.

---

## 2. Risk Summary (Dashboard)

| #   | Finding                                                         | Severity   | Production Blocker? |
|-----|-----------------------------------------------------------------|------------|---------------------|
| 3.1 | Secrets committed to repository (.env.example)                  | CRITICAL   | Yes -- must rotate |
| 3.2 | Plaintext password storage                                      | CRITICAL   | Yes                |
| 3.3 | Wildcard CORS on both apps                                      | HIGH       | Yes                |
| 3.5 | Unrestricted filesystem path scanning                           | HIGH       | Yes                |
| 3.6 | No authorization on admin endpoints                             | HIGH       | Yes                |
| 3.8 | Raw document text sent to external AI provider                  | HIGH       | Requires GDPR DPIA |
| 3.4 | SQLite limitations for multi-user/multi-worker                  | MEDIUM     | Gate                |
| 3.7 | No audit trail for review/delete actions                        | MEDIUM     | Gate                |
| 3.9 | Logging potentially exposes PII/sensitive values                | MEDIUM     | No                  |
| 3.10 | File serving without path traversal protection                  | MEDIUM     | Gate                |
| 3.11 | Database URL hardcoded to SQLite                                | MEDIUM     | Gate                |

**Legend:**
- **Production Blocker:** Must be resolved before industrial deployment.
- **Gate:** Blocks scaling or compliance certification; must be addressed early in productionization.
- **No:** Acceptable for early-stage production with mitigations; track in backlog.

---

## 3. Detailed Findings

### 3.1 Secrets Exposed in Repository

**Severity:** CRITICAL
**Production Blocker:** Yes

**Description:** The file `.env.example` contained a live OpenRouter API key (`sk-or-v1-f7edd22c...`). This key was committed to the repository and visible in git history. Even after rewriting `.env.example` in this pass, the key remains accessible in prior commits.

**Impact:**
- Anyone with repository access (public or internal) can use the API key.
- Depending on the OpenRouter account configuration, this could incur financial charges or expose the account.
- The key cannot be truly removed from git history without a force-push rewrite.

**Proposed Remediation:**
1. **IMMEDIATE:** Rotate the compromised key at https://openrouter.ai/keys.
2. **This pass (done):** `.env.example` now contains a placeholder (`sk-or-v1-your-key-here`) instead of a live key.
3. **Optional:** Run `git filter-branch` or `BFG Repo-Cleaner` to purge the key from history, then force-push. Coordinate with all contributors before doing this.
4. **Process:** Add a pre-commit hook that scans for secret patterns (e.g., `sk-or-`, `-----BEGIN PRIVATE KEY-----`).

---

### 3.2 Plaintext Password Storage

**Severity:** CRITICAL
**Production Blocker:** Yes

**Where:**
- `database.py` line 23: `password = Column(String) # Plaintext is fine for a hackathon!`
- `main.py` lines 41-48: Admin seeded with `password="password123"`
- `main.py` lines 73-77: Every demo employee seeded with `password="password123"`
- `main.py` line 1307: Dummy employee seeded with `password="password123"`
- `main.py` line 196: Login comparison: `if not user or user.password != password`
- `seed_json_data.py`: Also references plaintext passwords

**Impact:**
- Any database dump exposes all user passwords.
- An insider with DB access can impersonate any user.
- Violates GDPR principle of "Security of Processing" (Article 32) -- passwords are personal data and must be protected.
- Cannot meet basic security certification requirements (SOC 2, ISO 27001).

**Proposed Remediation:**
- Phase 2 (Agent 12): Hash passwords with `bcrypt` or `passlib[bcrypt]`.
- Store only `password_hash`, not `password`.
- Update login flow: `passlib.verify(password, user.password_hash)`.
- Add rate limiting to login endpoint (e.g., 5 attempts per minute per IP).
- Migration script to hash all existing plaintext passwords.

**This pass:** Added `⚠️ DEMO-ONLY` annotations in `database.py` and `main.py` login.

---

### 3.3 Wildcard CORS Configuration

**Severity:** HIGH
**Production Blocker:** Yes

**Where:**
- `main.py` lines 153-159: `allow_origins=["*"]`, `allow_credentials=True`
- `api.py` lines 439-445: `allow_origins=["*"]`, `allow_credentials=True`

**Impact:**
- Any website on the internet can make authenticated requests to the API (when `allow_credentials` is `true` and `allow_origins` is `*`, browsers may reject the request, but non-browser clients are unaffected).
- Combined with the weak session cookie, this enables cross-origin attacks from malicious pages.
- CORS `allow_origins=["*"]` with `allow_credentials=True` is technically invalid per the Fetch spec -- some browsers will reject it outright, but the presence of both flags signals a misunderstanding of CORS semantics.

**Proposed Remediation:**
- Set `allow_origins` to an explicit list of frontend origin(s), e.g., `["https://gdpr-dashboard.bosch.com"]`.
- Load origins from an environment variable (`CORS_ORIGINS`) with a safe default.
- Remove `allow_credentials=True` unless the frontend actually sends credentials cross-origin (and if so, specific origins are required by spec).

**This pass:** Added `⚠️ DEMO-ONLY` annotations in both files.

---

### 3.4 SQLite Limitations for Multi-User / Multi-Worker

**Severity:** MEDIUM
**Production Blocker:** Gate (blocks concurrent use)

**Where:** `database.py` lines 6-11

```python
SQLALCHEMY_DATABASE_URL = "sqlite:///./bosch_gdpr.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
```

**Impact:**
- **Single-writer limitation:** SQLite allows only one writer at a time. Under concurrent scan jobs or multiple API workers, writes will fail with `SQLITE_BUSY` or queue up serially.
- **No connection pooling:** Each worker process must open its own connection; the `check_same_thread=False` workaround is not safe for multi-process deployments (e.g., gunicorn with multiple workers).
- **No row-level locking:** Bulk inserts compete with read queries, causing latency spikes on the dashboard during scans.
- **No native replication or backups:** Cannot set up read replicas or point-in-time recovery.
- **File-based DB is lost if the container/pod restarts without a persistent volume.**

**Proposed Remediation:**
- Phase 2 (Agent 9): Migrate to PostgreSQL with Alembic for schema migrations.
- Until then, ensure `bosch_gdpr.db` is on a persistent volume.
- For intermediate scale: use WAL mode (`PRAGMA journal_mode=WAL`) to allow concurrent reads during writes.
- Add a connection pool size limit and retry logic for `SQLITE_BUSY`.

---

### 3.5 Unrestricted Filesystem Path Scanning

**Severity:** HIGH
**Production Blocker:** Yes

**Where:**
- `api.py` `POST /api/scan` -- accepts arbitrary `folder_path` from request body
- `main.py` `POST /api/admin/trigger-scan` -- accepts `target_dir` (defaults to `./demo_drive_rich`)
- `main.py` `POST /api/admin/trigger-extraction` -- accepts `target_dir` (defaults to `./demo_drive_rich`)

**Impact:**
- An authenticated user can scan any directory the server process has read access to (e.g., `/etc/`, `/home/`, mounted network drives).
- On a shared server, this could expose other users' documents.
- The scan engine processes and stores content from arbitrary paths, so a path like `/etc/passwd` would be parsed as if it were a legitimate document.
- No filesystem sandboxing or chroot confinement is in place.

**Proposed Remediation:**
- Restrict scan directories to an allow-list configured via environment variable (e.g., `SCAN_ALLOWED_ROOTS=/data/corporate_docs,/data/hr_docs`).
- Validate that the resolved path is a child of an allowed root before proceeding.
- Run the scan worker under a dedicated OS user with minimal filesystem permissions.
- Add path sanitization in `Path(...).resolve()` usage to prevent symlink escapes.

**This pass:** Added `⚠️ DEMO-ONLY` annotation on `api.py` `POST /api/scan`.

---

### 3.6 Missing Authorization on Admin Endpoints

**Severity:** HIGH
**Production Blocker:** Yes

**Where:** Throughout `main.py`, admin checks follow this pattern:

```python
if session_emp_id != "BX-ADMIN":
    raise HTTPException(status_code=403, detail="Forbidden")
```

Or simply check for cookie presence:
```python
if not session_emp_id:
    return RedirectResponse(url="/")
```

**Impact:**
- The session cookie (`session_emp_id`) is an unsigned plaintext string. Anyone can set `Cookie: session_emp_id=BX-ADMIN` in their browser and gain full admin access.
- There is no role-based access control -- a single hardcoded employee ID (`BX-ADMIN`) gates all admin functions.
- No session expiry, no logout mechanism, no token rotation.
- No differentiation between admin roles (super admin, DPO, auditor).
- The `POST /api/admin/deletion-request` endpoint does not verify the caller is actually an admin before sending notifications.

**Proposed Remediation:**
- Phase 2 (Agent 12): Implement proper authentication (OAuth2/OIDC or JWT-based).
- Phase 2 (Agent 12): Implement role-based access control with roles stored in DB (not hardcoded string comparison).
- Phase 2 (Agent 12): Add signed sessions with expiry (Starlette `SessionMiddleware` with a secret key).
- Short-term: At minimum, require that the session value is HMAC-signed so it cannot be forged.

**This pass:** Added `⚠️ DEMO-ONLY` annotations on the cookie set and admin seed code.

---

### 3.7 Missing Audit Trail

**Severity:** MEDIUM
**Production Blocker:** Gate (blocks compliance certification)

**Description:** The system performs sensitive operations -- review actions, file deletions, retention extensions, admin-triggered employee deletion requests -- but none of these actions are recorded in an immutable audit log.

**Where:**
- `api.py` `POST /api/findings/{finding_id}/review` -- review decisions logged only to application logger
- `main.py` `POST /api/employee/action` -- employee actions (delete, keep, false_positive) have no audit record
- `main.py` `POST /api/employee/files/{file_id}/delete-expired` -- physical file deletion with no durable record
- `main.py` `POST /api/admin/deletion-request` -- admin notification requests not audited

**Impact:**
- Cannot prove who reviewed what and when (GDPR accountability, Article 5(2)).
- Cannot reconstruct the sequence of actions for an incident investigation.
- Cannot meet SOC 2 or ISO 27001 audit log requirements.
- Application logs (stdout) are volatile and not tamper-resistant.

**Proposed Remediation:**
- Phase 2 (Agent 12): Add an `AuditLog` table with: timestamp, actor_id, action, target_type, target_id, old_value, new_value, ip_address, user_agent.
- Write audit entries in the same DB transaction as the action (atomicity).
- Never allow deletion or modification of audit records (append-only in application code; DB-level read-only permissions for the app user on the audit table).
- Consider a separate append-only audit DB or log sink for tamper resistance.

---

### 3.8 AI Data Sharing with OpenRouter

**Severity:** HIGH
**Production Blocker:** Requires GDPR DPIA (Data Protection Impact Assessment)

**Where:** `src/ai_parser.py` -- the `parse()` method sends up to 3000 characters of raw document text to OpenRouter API.

**Impact:**
- Raw document text -- potentially containing names, emails, passport numbers, IBANs, addresses -- is transmitted to a third-party LLM provider (OpenRouter, which may route to various model providers).
- This constitutes an **international data transfer** under GDPR Articles 44-49 if the model provider's servers are outside the EU/EEA.
- OpenRouter's privacy policy and data processing terms must be reviewed to determine:
  - Whether they act as a data processor (DPA required) or controller.
  - Whether prompts are logged or used for model training.
  - Where data is processed geographically.
- The system prompt instructs the AI to detect PII, but this is self-defeating: the PII is already being sent externally to detect that it exists.
- No data minimization is applied before sending -- the full first 3000 chars of each document are sent regardless of sensitivity.

**Proposed Remediation:**
- **Pre-filter approach:** Run local regex detection first (already done). Only send documents to AI that warrant deeper analysis AND have been flagged by a human reviewer.
- **Data minimization:** Redact known PII values from the text before sending (replace emails with `[EMAIL_REDACTED]`, etc.).
- **On-premise LLM:** Deploy an open-source model (e.g., Llama, Mistral) on EU-hosted infrastructure.
- **EU-hosted provider:** Use an OpenAI/Azure EU region deployment with a DPA in place, or a provider that guarantees EU-only data processing.
- **DPIA:** Conduct a formal Data Protection Impact Assessment before enabling AI scanning with real corporate documents.
- Add a config flag (`AI_ENABLED=false`) so AI scanning is opt-in and can be disabled in sensitive environments.

**This pass:** Added `⚠️ DEMO-ONLY / COMPLIANCE NOTE` docstring in `src/ai_parser.py`.

---

### 3.9 Logging of Sensitive Data

**Severity:** MEDIUM
**Production Blocker:** No (can be remediated incrementally)

**Where:** Multiple locations emit log messages with potentially sensitive data:

- `api.py` line 334: Logs scan_id, file count, finding count, timing -- generally safe.
- `api.py` line 491: Logs folder path -- could reveal internal directory structure.
- `api.py` line 334: Detailed scan completion log -- safe.
- `api.py` line 943-948: Logs reviewer name, action, finding_id -- reviewer identity is PII.
- `main.py` line 654-657: `logging.info("RETENTION JUSTIFICATION — file=%s reason=%s project=%s admin=%s notes=%s", ...)` -- logs admin email and potentially sensitive project/notes content.
- `main.py` line 165: `mask_sensitive_data()` function is defined but inconsistently used.

**Impact:**
- Logs captured by log aggregators (CloudWatch, Datadog, Splunk) may retain PII indefinitely.
- Reviewer identities, admin emails, and file paths are personal data under GDPR.
- Debug-level logging in production could accidentally expose finding values.

**Proposed Remediation:**
- Define a logging policy: never log raw PII values (emails, names, IDs) at INFO level or above.
- Replace reviewer names/emails with anonymized IDs in logs.
- Use structured logging (JSON) with a PII-redaction filter.
- Move detailed payload logging to DEBUG level and disable DEBUG in production.
- Review all `logger.info()` calls for sensitive data before production deployment.

**This pass:** Noted the pattern. The `mask_sensitive_data()` function in `main.py` is a good starting point but only used in the admin KPI endpoint.

---

### 3.10 File Serving Without Path Traversal Protection

**Severity:** MEDIUM
**Production Blocker:** Gate

**Where:** `main.py` lines 1754-1781: `GET /api/files/{file_id}/view`

```python
file_path = file_meta.file_path
...
return FileResponse(file_path)
```

**Impact:**
- The endpoint serves files directly from the path stored in the `FileMetadata` table.
- If a malicious or buggy scan populates the DB with a path like `../../../etc/passwd`, the file viewer would serve that file.
- While only the file owner can view (via `session_emp_id` check), this defense is weakened by the forgeable session cookie (see 3.6).
- No check that the resolved path is within an allowed directory root.

**Proposed Remediation:**
- Resolve the path and verify it is within an allowed root directory.
- Use `os.path.realpath()` to resolve symlinks before checking.
- Only serve files from known data directories.
- Consider streaming file contents rather than exposing filesystem paths.

---

### 3.11 Database URL Hardcoded to SQLite

**Severity:** MEDIUM
**Production Blocker:** Gate

**Where:** `database.py` line 6:

```python
SQLALCHEMY_DATABASE_URL = "sqlite:///./bosch_gdpr.db"
```

**Impact:**
- Cannot switch databases without code changes.
- No environment-variable override path.
- Inconsistent with `.env.example` which suggests `DATABASE_URL` but it is never read.

**Proposed Remediation:**
- Read `DATABASE_URL` from environment with SQLite as fallback:
  ```python
  SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bosch_gdpr.db")
  ```
- Phase 2 (Agent 11): Move all configuration to a structured config module.

---

## 4. What Is Already Safe

These patterns in the codebase are already production-aligned and should be preserved:

| Pattern                                              | Location                          | Notes                                                                     |
|------------------------------------------------------|-----------------------------------|---------------------------------------------------------------------------|
| `mask_sensitive_data()` for admin dashboards         | `main.py` line 166                | Admin KPIs show masked snippets (`J***n` instead of `John`). Well done.   |
| Owner-only file viewing enforcement                  | `main.py` line 1773               | Only the file's owner can view raw file contents. Admin CANNOT.           |
| PII value deduplication before DB insert             | `main.py` lines 1050, 1217        | Prevents duplicate findings. Reduces noise for reviewers.                 |
| `.env` in `.gitignore`                               | `.gitignore` line 11              | Prevents local `.env` from being committed.                              |
| `*.db` in `.gitignore`                               | `.gitignore` line 8               | Prevents the SQLite database from being committed.                        |
| Bulk DB writer with batch flushing                   | `src/db_writer.py`                | Efficient persistence pattern. Good for production scale.                |
| Graceful AI parser fallback                          | `api.py` lines 64-69, `main.py` line 967 | System works without AI if API key is unavailable.                       |
| MIME type validation on image upload                 | `main.py` lines 1659-1666         | Whitelist approach; blocks unexpected file types.                         |
| Immediate memory cleanup after OCR                   | `main.py` line 1730               | `del file_bytes` after processing. Good memory hygiene.                   |
| Temp directory cleanup in finally block              | `api.py` line 701                  | `shutil.rmtree` always runs. No temp file leakage.                        |

---

## 5. Remediation Roadmap

This reflects the phased approach from `MULTI_AGENT_REFACTOR_PLAN.md`.

### Phase 2: Industrial Middleware (immediate next steps)

| Remediation                      | Agent | Priority |
|----------------------------------|-------|----------|
| Migrate to PostgreSQL + Alembic  | 9     | High     |
| Background job queue (Celery/RQ) | 10    | High     |
| Structured config + env vars     | 11    | High     |
| Real auth (OAuth2/JWT) + RBAC    | 12    | High     |
| Audit trail (append-only log)    | 12    | High     |
| Observability (metrics, alerts)  | 13    | Medium   |

### Phase 3: Data Governance Enhancements

| Remediation                            | Priority |
|----------------------------------------|----------|
| Connector auth + delta tokens           | High     |
| Retention policy engine                 | High     |
| Legal basis metadata per document       | Medium   |
| DPO escalation workflow                 | Medium   |
| Immutable audit log (WORM storage)      | Medium   |
| Data minimization in AI pipeline        | High     |
| Human review SLAs                       | Low      |
| Fine-grained permissions per document   | Medium   |
| AI governance (model registry, review)  | Medium   |

### Quick Wins (can be done in any pass)

1. Rotate the exposed OpenRouter API key immediately.
2. Add `DATABASE_URL` env var read in `database.py`.
3. Add `CORS_ORIGINS` env var read in both `main.py` and `api.py`.
4. Add a `[tool.bandit]` or `[tool.detect-secrets]` config to CI.
5. Add `pragma journal_mode=WAL` to SQLite engine options for better concurrency.
6. Move `mask_sensitive_data()` into a shared utility module and apply consistently.

---

## 6. Summary of This Pass

### Files Changed

1. **`.env.example`** -- Replaced live API key with placeholder. Documented all env vars.
2. **`database.py`** -- Added `⚠️ DEMO-ONLY` annotation on plaintext password column.
3. **`main.py`** -- Added `⚠️ DEMO-ONLY` annotations on:
   - Wildcard CORS middleware
   - Plaintext password login comparison
   - Unsigned session cookie
   - Hardcoded admin account seeding
4. **`api.py`** -- Added `⚠️ DEMO-ONLY` annotations on:
   - Wildcard CORS middleware
   - Unrestricted filesystem path scanning
5. **`src/ai_parser.py`** -- Added compliance note about AI data sharing with external provider.

### Risks / Follow-Ups

- **CRITICAL:** The OpenRouter API key remains in git history. The key must be rotated at the provider.
- The `.env.example` now has placeholder text, but no pre-commit hook is in place to prevent future secret commits.
- No new tests were written -- this was a documentation-and-annotation pass only.

### Intentionally Not Touched

- Did not implement any auth system or password hashing (deferred to Agent 12).
- Did not change CORS settings in code (added annotations only).
- Did not restrict filesystem path scanning (added annotation only).
- Did not add an audit trail table (deferred to Agent 12).
- Did not modify AI data sharing behavior (added documentation only).
- Did not modify the DB engine or connection setup.
- Did not move or delete any source files.
- Did not modify `seed_json_data.py` or any root-level scripts.

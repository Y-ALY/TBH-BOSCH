# UI Route & API Dependency Map

**Date:** 2026-06-01
**Author:** Agent 8 (Frontend/UI Route Stabilization)
**Status:** Living document -- update as routes are moved to `app/routes/`.

---

## 1. Template to Route Mapping

| Template | Route | Method | Handler(s) | Auth Required |
|----------|-------|--------|------------|---------------|
| `login.html` | `GET /` | GET | `login_page` | No |
| `login.html` (on error) | `POST /login` | POST | `login` | No |
| `admin_dashboard.html` | `GET /admin-dashboard` | GET | `admin_dashboard` | Yes (any session) |
| `admin_database_explorer.html` | `GET /admin-database-explorer` | GET | `admin_database_explorer` | Yes (any session) |
| `employee_dashboard.html` | `GET /employee-dashboard` | GET | `employee_dashboard` | Yes (any session) |
| `employee_directory.html` | `GET /employee-directory` | GET | `employee_directory` | Yes (any session) |
| `data_points.html` | `GET /data-points` | GET | `data_points` | Yes (any session) |
| `user_details.html` | `GET /user-details/{employee_id}` | GET | `user_details` | Yes (any session) |

**Note on task description vs actual routes:** The task description listed `/admin/data-points` and `/admin/database-explorer`. The actual routes in `main.py` are `/data-points` and `/admin-database-explorer` (kebab-case, no sub-path).

All 6 page-rendering routes plus the login route are defined in `main.py` and verified to render the correct template. No route redirects to a missing or wrong template.

---

## 2. JS API Call Mapping

### 2.1 Static JS File

`static/js/dashboard.js` -- Pure frontend UI code (particle canvas, theme toggle, sidebar, scroll reveals). **Contains zero API calls.** All API calls live inline in `<script>` blocks within each template.

### 2.2 API Calls by Template

#### `admin_dashboard.html`

| API Endpoint | Method | Line(s) | Purpose |
|-------------|--------|---------|---------|
| `/api/admin/kpis` | GET | 301, 316, 422 | Load KPI metrics + recent alerts for dashboard |
| `/api/employee/action` | POST | 403 | Process admin actions on findings (false_positive, keep, delete, etc.) |

#### `admin_database_explorer.html`

| API Endpoint | Method | Line(s) | Purpose |
|-------------|--------|---------|---------|
| `/api/admin/trigger-scan` | POST | 953 | Trigger a delta-aware scan on demo_drive_rich |
| `/api/admin/employees/search?q=...` | GET | 971 | Search employees by name/email/ID |
| `/api/compliance-score/{emp.employee_id}` | GET | 990 | Fetch compliance score per employee |
| `/api/user-details/{emp.employee_id}` | GET | 1069 | Fetch user file/finding details |
| `/api/admin/deletion-request` | POST | 1260 | Send deletion request notification to employee |
| `/api/admin/retain-document/{file_id}` | POST | 1393 | Mark document as business-critical with reason |

#### `employee_dashboard.html`

| API Endpoint | Method | Line(s) | Purpose |
|-------------|--------|---------|---------|
| `/api/employee/files/{employee_id}` | GET | 349 | Load employee's files with pending findings |
| `/api/employee/files/{file_id}/delete-expired` | POST | 449 | Delete expired file |
| `/api/employee/files/{file_id}/extend-retention` | POST | 492 | Extend file retention by 90 days |
| `/api/employee/action` | POST | 547 | Process action on finding (keep, delete, false_positive, etc.) |
| `/api/user-details/{employee_id}` | GET | 566 | Load user profile data |
| `/api/compliance-score/{employee_id}` | GET | 634 | Load compliance/data-hygiene score |
| `/api/employee/notifications/{employee_id}` | GET | 705 | Load notification list |
| `/api/employee/notifications/{notifId}/read` | POST | 744 | Mark notification as read |

#### `employee_directory.html`

| API Endpoint | Method | Line(s) | Purpose |
|-------------|--------|---------|---------|
| `/api/admin/employees/search?q=...` | GET | 251 | Search employees (admin-only API) |

**Note:** This template calls `/api/admin/employees/search` which is admin-only (returns 403 for non-admin). The directory page appears designed for admin use.

#### `data_points.html`

| API Endpoint | Method | Line(s) | Purpose |
|-------------|--------|---------|---------|
| `/api/user-details/{employee_id}` | GET | 284 | Load user file data for data points view |

#### `user_details.html`

| API Endpoint | Method | Line(s) | Purpose |
|-------------|--------|---------|---------|
| `/api/user-details/{employee_id}` | GET | 384 | Load full user file/finding details |
| `/api/employee/files/{file_id}/delete-expired` | POST | 646 | Delete expired file |
| `/api/employee/files/{file_id}/extend-retention` | POST | 689 | Extend retention deadline |
| `/api/admin/extend-retention/{file_id}` | POST | 731 | Admin-side retention extension |

#### `gdpr_modal_snippet.html` (included by other templates)

| API Endpoint | Method | Line(s) | Purpose |
|-------------|--------|---------|---------|
| `/api/employee/files/{file_id}/delete-expired` | POST | 215 | Delete expired file from modal |

### 2.3 Unused API Endpoints (in main.py but no frontend call found)

These endpoints exist in `main.py` but are not called by any template:

| API Endpoint | Method | Notes |
|-------------|--------|-------|
| `/api/admin/trigger-extraction` | POST | Memory-safe extraction; may be triggered via admin panel not captured in templates or via direct API |
| `/api/admin/extraction-results` | GET | Returns cached extraction results |
| `/api/admin/seed-dummy-data` | POST | One-time seed utility |
| `/api/search` | GET | Search findings; not used by current templates |
| `/api/scan/image` | POST | OCR image upload; no UI component found |
| `/api/scan/image/cache-stats` | GET | OCR cache debug |
| `/api/scan/image/clear-cache` | POST | OCR cache management |
| `/api/files/{file_id}/view` | GET | File content viewer (owner-only) |

These are not broken -- they are server-side endpoints that could be called by other tools, CLI, or future UI. No action needed.

---

## 3. Access Control Matrix

### Page Routes

| Route | Admin (BX-ADMIN) | Employee (other) | Unauthenticated |
|-------|-------------------|-------------------|-----------------|
| `GET /` | Login page | Login page | Login page |
| `GET /admin-dashboard` | Dashboard | Dashboard (same page, different data) | Redirect to `/` |
| `GET /admin-database-explorer` | Full access | Full access (no role check) | Redirect to `/` |
| `GET /employee-dashboard` | Dashboard | Dashboard | Redirect to `/` |
| `GET /employee-directory` | Full access | Full access | Redirect to `/` |
| `GET /data-points` | Full access | Full access | Redirect to `/` |
| `GET /user-details/{id}` | Full access | Full access | Redirect to `/` |

**Observation:** Page routes check for ANY valid session cookie but do NOT enforce role-based access. Both admin and employee users can access all pages. Role enforcement is done at the API level, not the page level.

### API Routes

| API Endpoint | Admin | Employee (own data) | Employee (other data) | Unauthenticated |
|-------------|-------|---------------------|-----------------------|-----------------|
| `GET /api/admin/kpis` | Yes | 403 | 403 | 401 |
| `GET /api/admin/employees/search` | Yes | 403 | 403 | 401 |
| `POST /api/admin/trigger-scan` | Yes (no check) | Yes (no check) | Yes | 401 |
| `POST /api/admin/extend-retention/{file_id}` | Yes (mock) | Yes (mock) | Yes | 401 |
| `POST /api/admin/retain-document/{file_id}` | Yes | Yes | Yes | 401 |
| `POST /api/admin/deletion-request` | Yes | Yes | Yes | 401 |
| `POST /api/admin/trigger-extraction` | Yes | Yes | Yes | 401 |
| `GET /api/admin/extraction-results` | Yes | Yes | Yes | 401 |
| `POST /api/admin/seed-dummy-data` | Yes | Yes | Yes | 401 |
| `GET /api/user-details/{id}` | Yes | Own only | 403 | 401 |
| `GET /api/employee/files/{id}` | Yes | Own only | 403 | 401 |
| `POST /api/employee/action` | Yes | Yes | Yes | 401 |
| `POST /api/employee/files/{id}/delete-expired` | Yes (all) | Own only | 403 | 401 |
| `POST /api/employee/files/{id}/extend-retention` | Yes (all) | Own only | 403 | 401 |
| `GET /api/employee/notifications/{id}` | Yes | Own only | 403 | 401 |
| `POST /api/employee/notifications/{id}/read` | Yes | Yes | Yes | 401 |
| `GET /api/compliance-score/{id}` | Yes | Own only | 403 | 401 |
| `GET /api/search` | Yes (all data) | Own files only | Own files only | 401 |

**Key findings:**
1. Several `/api/admin/*` routes lack admin-only checks (e.g., `trigger-scan`, `retain-document`, `deletion-request`). Any authenticated user can call them.
2. Employee routes correctly scope data to the requesting employee (unless admin).
3. The `POST /api/admin/extend-retention/{file_id}` route is a mock -- always returns success without DB changes.

---

## 4. Static Assets

### Static Files Inventory

| File | Used By | Status |
|------|---------|--------|
| `static/css/login.css` | `login.html` | Active |
| `static/css/dashboard.css` | All dashboard pages (6 templates) | Active |
| `static/js/dashboard.js` | All dashboard pages (6 templates) | Active |

### Static Reference Audit

All `<link>` and `<script>` tags in templates were verified against the actual files on disk:

| Template | CSS Ref | JS Ref | Both Exist? |
|----------|---------|--------|-------------|
| `login.html` | `/static/css/login.css` | (none) | Yes |
| `admin_dashboard.html` | `/static/css/dashboard.css?v=8` | `/static/js/dashboard.js?v=6` | Yes |
| `admin_database_explorer.html` | `/static/css/dashboard.css?v=6` | `/static/js/dashboard.js?v=6` | Yes |
| `employee_dashboard.html` | `/static/css/dashboard.css?v=8` | `/static/js/dashboard.js?v=6` | Yes |
| `employee_directory.html` | `/static/css/dashboard.css?v=6` | `/static/js/dashboard.js?v=6` | Yes |
| `data_points.html` | `/static/css/dashboard.css?v=6` | `/static/js/dashboard.js?v=6` | Yes |
| `user_details.html` | `/static/css/dashboard.css?v=6` | `/static/js/dashboard.js?v=6` | Yes |

**Result: Zero broken static references.** Every `/static/...` path in every template resolves to an existing file. Cache-busting query strings (`?v=6`, `?v=8`) differ between templates but are harmless.

No templates reference external CDN scripts (no jQuery, no axios). All dependencies are local.

---

## 5. Manual Smoke Checklist

### Prerequisites
- Server running: `uvicorn main:app --reload --port 8000`
- DB file `bosch_gdpr.db` will be auto-created on first startup
- Demo data seeded automatically from `demo_drive_rich/` and `demo_drive_rich/owner_hints.json`

### Test Credentials
| Role | Email | Password | Employee ID |
|------|-------|----------|-------------|
| Admin | admin@bosch.com | password123 | BX-ADMIN |
| Employee | (varies per demo_drive_rich) | password123 | (auto-generated) |

### Checklist

1. **[ ] Login as admin**
   - Open `http://localhost:8000/`
   - Select "Admin" role
   - Enter `admin@bosch.com` / `password123`
   - Should redirect to `/admin-dashboard`

2. **[ ] Admin dashboard loads**
   - KPI cards should show file counts, volume, flagged files
   - Recent alerts should display (masked values)
   - No console errors in browser dev tools

3. **[ ] Admin database explorer**
   - Navigate to Database Explorer via sidebar
   - Search for an employee
   - Verify employee list loads with file counts and compliance scores
   - Trigger a scan (button should exist on page)
   - Verify scan results appear (may be empty if no changed files)

4. **[ ] Admin directory and data points**
   - Navigate to Employee Directory (sidebar)
   - Search for employees
   - Navigate to Data Points
   - Click an employee row to see their files

5. **[ ] Login as employee**
   - Log out (if no logout button, clear cookies or use incognito)
   - Open `http://localhost:8000/`
   - Select "Employee" role
   - Enter employee email from `demo_drive_rich/owner_hints.json` (any listed email) / `password123`
   - Should redirect to `/employee-dashboard`

6. **[ ] Employee dashboard loads**
   - Should show assigned files with findings
   - Compliance score should display
   - Notifications panel should load

7. **[ ] Employee actions**
   - Click on a finding and perform an action (keep, delete, false_positive)
   - Action should process without error
   - Extend retention on a file
   - Delete expired file (if any exist)

8. **[ ] Cross-route navigation**
   - Click through all sidebar links
   - Verify each page loads without console errors
   - Verify no 404s in network tab

9. **[ ] Static assets**
   - Verify dashboard styling loads (dark theme by default)
   - Verify theme toggle works (light/dark)
   - Verify particles canvas renders on dashboard pages
   - Verify login page uses login.css (different styling)

10. **[ ] API smoke test (terminal)**
    ```bash
    # With server running on port 8000:
    curl -s http://localhost:8000/api/admin/kpis -H 'Cookie: session_emp_id=BX-ADMIN' | python3 -m json.tool
    curl -s 'http://localhost:8000/api/admin/employees/search?q=a' -H 'Cookie: session_emp_id=BX-ADMIN' | python3 -m json.tool
    ```

---

## 6. Findings & Observations

### Verified Correct
- All 7 page-rendering routes in `main.py` correctly point to their respective templates
- All 14 API endpoints called by the frontend exist in `main.py`
- All 3 static files referenced by templates exist on disk
- Zero broken `<script>` or `<link>` tags

### Deviation from Task Description
The task description listed two incorrect routes:
- Task says `data_points.html` maps to `GET /admin/data-points`; actual is `GET /data-points`
- Task says `admin_database_explorer.html` maps to `GET /admin/database-explorer`; actual is `GET /admin-database-explorer`

### Observations
- `static/js/dashboard.js` is pure UI code with zero API calls. All data fetching is inline `<script>` in each template. This means each template has its own duplicated fetch logic.
- Several `/api/admin/*` endpoints do not enforce admin-only access (trigger-scan, retain-document, seed-dummy-data, deletion-request, trigger-extraction). This is noted but outside Agent 8 scope.
- Page routes check for session existence but do NOT enforce role-based access at the page level. An employee can navigate to `/admin-database-explorer` and the page renders (though API calls within will 403).
- No `app/routes/scan.py` exists yet -- this is planned for future agent work.
- No test file `tests/test_api_smoke.py` exists. The task's instruction to run it cannot be executed.

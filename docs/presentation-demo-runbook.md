# Presentation Demo Runbook

How to deploy and run the TBH-BOSCH GDPR scanner for a live presentation.

---

## Quick Deploy (Render)

### Step 1: Push to GitHub

Push this repo to a GitHub repository you control.

### Step 2: Create Render Blueprint

1. Go to [https://dashboard.render.com](https://dashboard.render.com) and sign in (GitHub OAuth works).
2. Click **New** > **Blueprint**.
3. Connect your GitHub repo.
4. Render reads `render.yaml` and creates:
   - A **Web Service** (`tbh-bosch`) running the FastAPI dashboard.
   - A **PostgreSQL** database (`tbh-bosch-db`, free tier).

### Step 3: Seed Demo Data

Once the service is deployed, open the Render Shell for the web service and run:

```bash
python scripts/demo/seed_demo_data.py
```

This creates the demo accounts, file metadata, and preseeded findings.

### Step 4: Open the App

Click the `.onrender.com` URL in the Render dashboard. You should see the login page.

---

## Run Locally for Demo

### Prerequisites

```bash
pip install -r requirements.txt
```

### Option A: SQLite (fastest, no external DB)

```bash
# Seed the demo data
python scripts/demo/seed_demo_data.py

# Start the web dashboard
uvicorn main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000).

### Option B: PostgreSQL (matches deployed setup)

```bash
# Set your local Postgres URL
export DATABASE_URL="postgresql://user:pass@localhost:5432/bosch_gdpr"

# Seed
python scripts/demo/seed_demo_data.py

# Start
uvicorn main:app --reload --port 8000
```

---

## Default Credentials

| Role   | Email              | Password      |
|--------|--------------------|---------------|
| Admin  | admin@bosch.com    | password123   |
| Employee | *(any from owner_hints.json)* | password123 |

All employee accounts share the password `password123`.

> **These are demo-only plaintext credentials.** Not for production use.

---

## Demo Flow (What to Show)

1. **Login** -- Show the login page, log in as admin (`admin@bosch.com` / `password123`).
2. **Admin Dashboard** -- Show KPIs: total files scanned, flagged files, data volume, expiring documents, recent alerts.
3. **Employee Directory** (`/employee-directory`) -- Search for employees, click one to see their files and findings.
4. **Findings List** (`/api/findings` or via dashboard) -- Browse detected PII: emails, phone numbers, IBANs, tax IDs, passport numbers, etc. Filter by risk level, type, or review status.
5. **Review Action** -- Pick a finding and mark it as "delete", "retain", "mask", or "false_positive". Show that the status updates immediately.
6. **Employee Dashboard** -- Log out, log in as an employee. Show the employee view: their files, pending actions, compliance score.
7. **Upload/Scan** (optional, if time) -- Upload a sample PDF via the scan endpoint or trigger a scan on a folder.

---

## Troubleshooting

### "Database connection refused" (Render)

- Wait 1-2 minutes after the Blueprint creates the DB. Render provisions databases asynchronously.
- Check that `DATABASE_URL` is set in the web service environment variables.

### "No data showing in dashboard"

- Run `python scripts/demo/seed_demo_data.py` from the Render Shell.
- Verify the database has rows: `python -c "from database import SessionLocal, Employee; db=SessionLocal(); print(db.query(Employee).count())"`.

### "Tables don't exist" errors

- The app calls `Base.metadata.create_all(bind=engine)` at import time. If tables still don't exist, run `python database.py` manually (it runs create_all on import).

### "Port already in use" (local)

- Kill the existing process: `lsof -ti:8000 | xargs kill -9`, then restart.

### "Module not found" errors

- Ensure you are running from the repo root.
- If running `seed_demo_data.py`, make sure the repo root is on `PYTHONPATH`.

---

## How to Upload a Sample PDF for Scan

### Option 1: Use the upload endpoint

```bash
curl -X POST http://localhost:8000/api/upload \
  -F "file=@path/to/sample.pdf"
```

### Option 2: Trigger a scan on a folder

```bash
curl -X POST http://localhost:8000/api/scan \
  -H "Content-Type: application/json" \
  -d '{"folder_path": "./sample_docs", "mode": "full", "ai_mode": "off"}'
```

### Option 3: Use the admin dashboard

1. Log in as admin.
2. Navigate to the admin dashboard.
3. Use the "Trigger Scan" or "Upload Files" controls.

---

## Render Free Tier Limits

- PostgreSQL: 1 GB storage, max 5 concurrent connections
- Web service: spins down after 15 minutes of inactivity, cold start ~30s
- The `NullPool` setting in `database.py` prevents idle connection exhaustion

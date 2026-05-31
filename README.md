# TBH-BOSCH

GDPR / PII document scanning demo built for the TechON Hackathon 2026 (Bosch problem statement).

Discovers PDFs and office documents, extracts text, detects personal data (PII/GDPR items), assigns data owners, and presents findings via a FastAPI web dashboard.

---

## Quick Deploy to Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/YOUR_ORG/tbh-bosch)

Or manually via Blueprint:

1. Push this repo to GitHub.
2. In [Render Dashboard](https://dashboard.render.com), click **New > Blueprint** and connect the repo.
3. Render provisions a web service + free PostgreSQL database automatically.
4. After deploy, open the Render Shell and run:
   ```bash
   python scripts/demo/seed_demo_data.py
   ```
5. Open the `.onrender.com` URL to see the login page.

See `docs/presentation-demo-runbook.md` for the full demo walkthrough.

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Seed demo data (SQLite by default)
python scripts/demo/seed_demo_data.py

# Start the web dashboard (login, admin, employee views)
uvicorn main:app --reload --port 8000

# Start the scan API (trigger scans, query findings, review actions)
uvicorn api:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000).

**Default credentials (demo only):**
- Admin: `admin@bosch.com` / `password123`
- Employees: any email from `demo_drive_rich/owner_hints.json` / `password123`

---

## Environment Variables

See `.env.example` for all options. Key ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./bosch_gdpr.db` | PostgreSQL URL (Render sets this automatically) |
| `SCAN_ROOT` | `./demo_drive_rich` | Directory to scan for demo data |
| `DEMO_MODE` | `true` | Enable demo-specific behavior |
| `OPENROUTER_API_KEY` | (none) | OpenRouter API key for AI classification |

---

## Project Structure

```
main.py                  # FastAPI web dashboard + mixed API routes
api.py                   # FastAPI scan API (separate app)
database.py              # SQLAlchemy ORM models
src/                     # Scan engine (scanner, classifier, owner, etc.)
templates/               # Jinja2 HTML templates
static/                  # CSS/JS assets
demo_drive_rich/         # Demo data (sample PDFs + owner_hints.json)
scripts/demo/            # Demo utilities (seed data script)
docs/                    # Documentation
render.yaml              # Render Blueprint deploy config
```

---

## CLI Tools

```bash
# Run a scan via CLI (no web server)
python -m src.pipeline full-scan --repo ./data/sample_pdfs --output ./data/output
python -m src.pipeline ai-scan --repo ./data/sample_pdfs --output ./data/output
```

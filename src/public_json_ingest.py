"""Ingest a public Google Drive / SharePoint JSON link into the GDPR database.

The Connect-External-Data flow points at a *public* share link (no credentials).
We fetch the JSON, then map each record to the same Employee / FileMetadata /
Finding rows that seed_json_data.py produces, so the whole dashboard renders.

Expected record shape (one of 500 in the user's dataset):

    {
      "id": "uuid",
      "employee_name": "Carl Molina",
      "department": "Engineering",
      "file_name": "debate_report_2004.txt",
      "file_type": "text/plain",
      "content": "...",
      "created_date": "2026-05-22",
      "retention_policy_days": 90,
      "extracted_text": null
    }
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

import requests

from database import Employee, FileMetadata, Finding


# ---------------------------------------------------------------------------
# Link parsing + fetch
# ---------------------------------------------------------------------------

def extract_drive_file_id(url: str) -> str | None:
    """Return the Google Drive file id from common public-link shapes, or None.

    Handles: /file/d/<id>/..., ?id=<id>, /uc?...id=<id>, and bare ids.
    Folder links (/folders/<id>) are resolved separately by _resolve_folder_file_id.
    """
    if not url:
        return None
    url = url.strip()

    m = re.search(r"/file/d/([a-zA-Z0-9_-]{20,})", url)
    if m:
        return m.group(1)

    m = re.search(r"[?&]id=([a-zA-Z0-9_-]{20,})", url)
    if m:
        return m.group(1)

    # A bare id pasted directly
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", url):
        return url

    return None


def _resolve_folder_file_id(folder_url: str) -> str | None:
    """Scrape a public Drive folder page for the first embedded file id.

    Public folders can't be listed without the Drive API, but the folder HTML
    embeds the contained file ids. We grab the first that yields downloadable
    JSON.  Returns None if nothing usable is found.
    """
    m = re.search(r"/folders/([a-zA-Z0-9_-]{20,})", folder_url)
    if not m:
        return None
    folder_id = m.group(1)

    resp = requests.get(f"https://drive.google.com/drive/folders/{folder_id}", timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Candidate ids look like file ids but exclude the folder id itself and
    # Google's API keys (which start with "AIza"). Preserve first-seen order.
    seen: list[str] = []
    for cid in re.findall(r"[\"\[]([a-zA-Z0-9_-]{25,44})[\"\],]", html):
        if cid == folder_id or cid.startswith("AIza") or cid in seen:
            continue
        seen.append(cid)

    # Probe candidates; return the first that downloads as JSON.
    for cid in seen:
        try:
            data = _download_json_by_id(cid)
            if data is not None:
                return cid
        except Exception:
            continue
    return None


def _download_json_by_id(file_id: str) -> list | dict | None:
    """Download a public Drive file by id and parse it as JSON, or None."""
    resp = requests.get(
        f"https://drive.google.com/uc?export=download&id={file_id}", timeout=60
    )
    resp.raise_for_status()
    try:
        return json.loads(resp.content)
    except (ValueError, UnicodeDecodeError):
        return None


def fetch_public_json(url: str) -> list[dict]:
    """Fetch JSON from a public Drive/SharePoint link and return a list of records.

    Accepts a direct file link, a folder link, a raw id, or a plain JSON URL.
    Raises ValueError with a clear message if the link can't be turned into JSON.
    """
    if not url or not url.strip():
        raise ValueError("No link provided.")
    url = url.strip()

    data: list | dict | None = None

    file_id = extract_drive_file_id(url)
    if file_id:
        data = _download_json_by_id(file_id)
    elif "/folders/" in url:
        file_id = _resolve_folder_file_id(url)
        if not file_id:
            raise ValueError(
                "Could not find a JSON file in that public folder. "
                "Share the JSON file directly (Anyone with the link)."
            )
        data = _download_json_by_id(file_id)
    else:
        # Treat as a plain URL that returns JSON (e.g. raw / SharePoint download link).
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        try:
            data = json.loads(resp.content)
        except (ValueError, UnicodeDecodeError):
            data = None

    if data is None:
        raise ValueError("The link did not return valid JSON.")

    # Accept either a top-level list or a {"records"/"cases"/...: [...]} wrapper.
    if isinstance(data, dict):
        for key in ("records", "cases", "data", "items", "files"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]

    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of records.")

    return [r for r in data if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# Mapping records -> DB rows
# ---------------------------------------------------------------------------

def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "Unknown", "User"
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _deterministic_emp_id(seed: str) -> str:
    """Stable BX- id from a seed string (same scheme as seed_json_data.py)."""
    n = int(hashlib.md5(seed.encode()).hexdigest(), 16) % 90000 + 10000
    return f"BX-{n}"


def _parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now()
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return datetime.now()


def ingest_records(db, records: list[dict], source_label: str = "external") -> dict:
    """Map JSON records to Employee / FileMetadata / Finding rows.

    One Finding ("Personal Data Record") per record, built from the structured
    fields. Owners are auto-created from employee_name. Returns count summary.
    """
    employees_created = 0
    files = 0
    findings = 0
    errors = 0

    # Cache employees by email within this run to avoid repeated lookups.
    emp_cache: dict[str, str] = {}
    # file_path has a UNIQUE constraint; seed from existing rows so appends
    # into a populated DB disambiguate against what's already there.
    used_paths: set[str] = {p for (p,) in db.query(FileMetadata.file_path).all()}
    # employee_id is UNIQUE too; track to resolve rare hash collisions at scale.
    used_emp_ids: set[str] = set()
    skipped = 0

    for rec in records:
        unique_path = None
        try:
            # Idempotent append: skip records already ingested from this link.
            finding_uid = f"ext-{rec.get('id')}" if rec.get("id") else None
            if finding_uid and db.query(Finding.id).filter(
                Finding.finding_uid == finding_uid
            ).first():
                skipped += 1
                continue
            name = (rec.get("employee_name") or "Unknown User").strip()
            dept = (rec.get("department") or "").strip()
            file_name = (rec.get("file_name") or rec.get("id") or "untitled").strip()
            content = rec.get("content") or ""
            retention_days = int(rec.get("retention_policy_days") or 0)
            created = _parse_date(rec.get("created_date"))

            # ── Employee (deterministic id + email from name) ──────────────
            first, last = _split_name(name)
            email = f"{name.lower().replace(' ', '.')}@bosch.com"
            emp_id = emp_cache.get(email)
            if emp_id is None:
                existing = db.query(Employee).filter(Employee.email == email).first()
                if existing:
                    emp_id = existing.employee_id
                else:
                    # Deterministic id, bumped on the rare hash collision so the
                    # employee_id UNIQUE constraint always holds at scale.
                    emp_id = _deterministic_emp_id(email)
                    while emp_id in used_emp_ids or db.query(Employee).filter(
                        Employee.employee_id == emp_id
                    ).first():
                        emp_id = _deterministic_emp_id(emp_id + "!")
                    used_emp_ids.add(emp_id)
                    db.add(Employee(
                        employee_id=emp_id,
                        email=email,
                        first_name=first,
                        last_name=last,
                        password="password123",  # demo universal password
                        department=dept,
                        location="",
                    ))
                    db.flush()  # assign before findings reference the owner
                    employees_created += 1
                emp_cache[email] = emp_id

            # ── FileMetadata ───────────────────────────────────────────────
            # Disambiguate duplicate file names (file_path is UNIQUE).
            unique_path = file_name
            if unique_path in used_paths:
                unique_path = f"{file_name} ({rec.get('id') or files})"
            used_paths.add(unique_path)

            retention_deadline = (
                created + timedelta(days=retention_days) if retention_days else None
            )
            file_meta = FileMetadata(
                file_path=unique_path,
                owner_employee_id=emp_id,
                size_bytes=len(content),
                file_hash=hashlib.md5(content.encode("utf-8", "replace")).hexdigest(),
                last_modified=created,
                retention_deadline=retention_deadline,
            )
            db.add(file_meta)
            db.flush()  # need file_meta.id for the finding

            # ── Finding (the structured record IS the personal data) ───────
            risk = "high" if retention_days and retention_days <= 90 else "medium"
            db.add(Finding(
                file_id=file_meta.id,
                category="PERSONAL DATA RECORD",
                confidence_score=1.0,
                flagged_snippet=f"{name} — {dept}" if dept else name,
                reasoning=f"Structured personal data ingested from {source_label}.",
                status="pending_review",
                review_status="pending_review",
                finding_uid=finding_uid or f"ext-{file_meta.id}",
                file_id_str=unique_path,
                type="personal_data",
                value=name,
                field="employee_name",
                context=dept or "unknown",
                risk_level=risk,
                confidence=1.0,
                evidence=f"Owner: {name}; Department: {dept}; Retention: {retention_days}d",
                recommended_action="review",
                assigned_owner=name,
                owner_email=email,
                owner_department=dept,
                owner_resolved=True,
                is_flagged=True,
            ))

            # Commit per record: keeps prior successes durable so a later
            # failure's rollback only discards the offending record.
            db.commit()
            files += 1
            findings += 1

        except Exception:
            # A failed flush/commit poisons the session — roll back so the
            # next record can proceed. Only the current record is lost.
            errors += 1
            used_paths.discard(unique_path)
            db.rollback()
            emp_cache.clear()  # ids flushed but rolled back are no longer valid
            continue

    return {
        "employees": employees_created,
        "files": files,
        "findings": findings,
        "skipped": skipped,
        "errors": errors,
    }

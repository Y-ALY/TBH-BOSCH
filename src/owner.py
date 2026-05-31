"""Dynamic owner assignment with 3-tier resolution.

Resolution priority:
  1. Static owner_hints (backward-compat if an owner_hints.json is supplied)
  2. Path-based:  extract employee_id from structured paths like
     /shared_drive/{employee_id}/... and query the Employee DB table.
  3. Document-based:  parse fields extracted from the document text
     (Name, Employee, Email, Manager) and match against the Employee DB.
  4. Fallback:  assign to 'Master of Data' / DPO if everything else fails.

All public functions mutate Finding objects in place.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from .models import Finding

logger = logging.getLogger(__name__)

# ── Regex for extracting an employee_id from a directory path ─────────────
# Matches patterns like:  /shared_drive/BX-1001/  or  /employees/EMP-00042/
_PATH_EMP_ID_RE = re.compile(
    r'(?:/|\\)'                    # directory separator
    r'((?:BX|EMP|HR)-?\d{3,8})'   # employee_id token
    r'(?:/|\\|$)',                 # followed by separator or end
    re.IGNORECASE,
)


def assign_owners(
    findings: list[Finding],
    hints: dict,
    *,
    file_path: str = "",
    fields: dict[str, str] | None = None,
    db_session=None,
) -> None:
    """Assign owners to findings using the 3-tier strategy.

    Args:
        findings:    List of Finding dataclasses to mutate.
        hints:       Static owner hints dict (from connector / JSON file).
        file_path:   The physical path of the scanned file.
        fields:      Extracted key:value fields from the document text.
        db_session:  An optional SQLAlchemy Session for Employee lookups.
    """
    if not findings:
        return

    # ── Tier 0: static hints override everything (backward compat) ───────
    if hints.get("name") and hints.get("email"):
        _apply(findings,
               name=hints["name"],
               email=hints["email"],
               department=hints.get("department", ""),
               resolved=True)
        return

    # ── Tier 1: path-based employee_id extraction ────────────────────────
    owner = _resolve_from_path(file_path, db_session)
    if owner:
        _apply(findings, **owner)
        return

    # ── Tier 2: document text field parsing ──────────────────────────────
    owner = _resolve_from_fields(fields or {}, db_session)
    if owner:
        _apply(findings, **owner)
        return

    # ── Tier 3: static hints site_owner / master_of_data fallback ────────
    if hints.get("site_owner"):
        _apply_fallback(findings, hints["site_owner"])
        return
    if hints.get("master_of_data"):
        _apply_fallback(findings, hints["master_of_data"])
        return

    # ── Tier 4: absolute fallback → DPO ──────────────────────────────────
    _apply_unresolved(findings)


# ═════════════════════════════════════════════════════════════════════════════
# Tier 1 – path-based resolution
# ═════════════════════════════════════════════════════════════════════════════

def _resolve_from_path(file_path: str, db_session) -> Optional[dict]:
    """Try to extract an employee_id from the file path, then look it up."""
    if not file_path:
        return None

    match = _PATH_EMP_ID_RE.search(file_path)
    if not match:
        return None

    emp_id = match.group(1).upper()
    logger.info("Path-based owner candidate: %s (from %s)", emp_id, file_path)

    if db_session is None:
        # No DB — can still assign the raw ID
        return dict(name=emp_id, email="", department="", resolved=True)

    employee = _query_employee_by_id(db_session, emp_id)
    if employee:
        return dict(
            name=f"{employee.first_name} {employee.last_name}",
            email=employee.email or "",
            department=employee.department or "",
            employee_id=employee.employee_id,
            resolved=True,
        )

    # ID found in path but not in DB — still assign the raw ID
    return dict(name=emp_id, email="", department="", resolved=True)


# ═════════════════════════════════════════════════════════════════════════════
# Tier 2 – document field parsing
# ═════════════════════════════════════════════════════════════════════════════

# Fields that commonly hold the document owner's name
_NAME_FIELDS = ("Employee", "Name", "Full Name", "Participant", "Reported by")
_EMAIL_FIELDS = ("Email", "E-Mail")
_DEPT_FIELDS = ("Department", "Abteilung")


def _resolve_from_fields(fields: dict[str, str], db_session) -> Optional[dict]:
    """Try to match extracted document fields against the Employee table."""

    # 1) Try email first — most precise
    for key in _EMAIL_FIELDS:
        email_val = fields.get(key, "").strip()
        if email_val and db_session is not None:
            employee = _query_employee_by_email(db_session, email_val)
            if employee:
                logger.info("Field-based owner match (email): %s", email_val)
                return dict(
                    name=f"{employee.first_name} {employee.last_name}",
                    email=employee.email or "",
                    department=employee.department or "",
                    employee_id=employee.employee_id,
                    resolved=True,
                )

    # 2) Try name fields — fuzzy match against first_name + last_name
    for key in _NAME_FIELDS:
        name_val = fields.get(key, "").strip()
        if not name_val or len(name_val) < 3:
            continue

        if db_session is not None:
            employee = _query_employee_by_name(db_session, name_val)
            if employee:
                logger.info("Field-based owner match (name): %s → %s",
                            name_val, employee.employee_id)
                return dict(
                    name=f"{employee.first_name} {employee.last_name}",
                    email=employee.email or "",
                    department=employee.department or "",
                    employee_id=employee.employee_id,
                    resolved=True,
                )

        # No DB or no match — still use the name from the document
        logger.info("Field-based owner (unverified): %s", name_val)
        dept_val = ""
        for dk in _DEPT_FIELDS:
            if fields.get(dk):
                dept_val = fields[dk]
                break
        email_val = ""
        for ek in _EMAIL_FIELDS:
            if fields.get(ek):
                email_val = fields[ek]
                break
        return dict(name=name_val, email=email_val, department=dept_val, resolved=True)

    return None


# ═════════════════════════════════════════════════════════════════════════════
# Database query helpers (import Employee lazily to avoid circular imports)
# ═════════════════════════════════════════════════════════════════════════════

def _query_employee_by_id(db_session, emp_id: str):
    """Look up an employee by their employee_id column."""
    try:
        from database import Employee
        return db_session.query(Employee).filter(
            Employee.employee_id == emp_id
        ).first()
    except Exception as exc:
        logger.debug("Employee lookup by ID failed: %s", exc)
        return None


def _query_employee_by_email(db_session, email: str):
    """Look up an employee by their email column."""
    try:
        from database import Employee
        return db_session.query(Employee).filter(
            Employee.email == email
        ).first()
    except Exception as exc:
        logger.debug("Employee lookup by email failed: %s", exc)
        return None


def _query_employee_by_name(db_session, name: str):
    """Fuzzy-match: try 'first last' against Employee.first_name + last_name."""
    try:
        from database import Employee
        parts = name.split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            result = db_session.query(Employee).filter(
                Employee.first_name.ilike(first),
                Employee.last_name.ilike(last),
            ).first()
            if result:
                return result

        # Fallback: check if any part of the name matches first or last name
        for part in parts:
            if len(part) < 2:
                continue
            result = db_session.query(Employee).filter(
                Employee.first_name.ilike(part) | Employee.last_name.ilike(part)
            ).first()
            if result:
                return result
    except Exception as exc:
        logger.debug("Employee lookup by name failed: %s", exc)
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Assignment helpers (mutate findings in-place)
# ═════════════════════════════════════════════════════════════════════════════

def _apply(
    findings: list[Finding],
    *,
    name: str,
    email: str,
    department: str,
    resolved: bool = True,
    employee_id: str = "",
) -> None:
    for f in findings:
        f.assigned_owner = employee_id if employee_id else name
        f.owner_email = email
        f.owner_department = department
        f.owner_resolved = resolved


def _apply_fallback(findings: list[Finding], label: str) -> None:
    for f in findings:
        f.assigned_owner = label
        f.owner_email = ""
        f.owner_department = ""
        f.owner_resolved = True
        f.escalation_target = "DPO_or_data_governance_team"


def _apply_unresolved(findings: list[Finding]) -> None:
    for f in findings:
        f.assigned_owner = "Master_of_Data"
        f.owner_email = ""
        f.owner_department = ""
        f.owner_resolved = False
        f.escalation_target = "DPO_or_data_governance_team"

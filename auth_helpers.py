"""Session helpers for admin vs employee API access."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from database import Employee


def is_admin_session(
    session_emp_id: Optional[str],
    session_role: Optional[str],
    db: Session,
) -> bool:
    if session_role == "admin":
        return True
    if session_emp_id in ("BX-ADMIN", "BX-17335"):
        return True
    if not session_emp_id:
        return False
    emp = db.query(Employee).filter(Employee.employee_id == session_emp_id).first()
    return bool(emp and emp.email == "admin@bosch.com")


def admin_effective_id(
    session_emp_id: Optional[str],
    session_role: Optional[str],
    db: Session,
) -> Optional[str]:
    if is_admin_session(session_emp_id, session_role, db):
        return "BX-ADMIN"
    return session_emp_id

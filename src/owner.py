"""Owner assignment with fallback escalation.

Single function: assign_owners() — mutates findings in place to set
owner fields based on connector hints.
"""

from __future__ import annotations

from .models import Finding


def assign_owners(findings: list[Finding], hints: dict) -> None:
    """Assign owners to findings based on owner hints.

    Priority:
      1. named individual (name + email present)  → resolved
      2. site_owner                                 → resolved with fallback
      3. master_of_data                             → resolved with fallback
      4. none of the above                          → unresolved, escalate to DPO

    Mutates each Finding in place.
    """
    name = hints.get("name", "")
    email = hints.get("email", "")
    department = hints.get("department", "")
    site_owner = hints.get("site_owner", "")
    master_of_data = hints.get("master_of_data", "")

    if name and email:
        _assign_person(findings, name, email, department)
    elif site_owner:
        _assign_site(findings, site_owner)
    elif master_of_data:
        _assign_mod(findings, master_of_data)
    else:
        _assign_unresolved(findings)


def _assign_person(
    findings: list[Finding], name: str, email: str, department: str
) -> None:
    for f in findings:
        f.assigned_owner = name
        f.owner_email = email
        f.owner_department = department
        f.owner_resolved = True


def _assign_site(findings: list[Finding], site_owner: str) -> None:
    for f in findings:
        f.assigned_owner = site_owner
        f.owner_email = ""
        f.owner_department = ""
        f.owner_resolved = True
        f.escalation_target = "DPO_or_data_governance_team"


def _assign_mod(findings: list[Finding], master_of_data: str) -> None:
    for f in findings:
        f.assigned_owner = master_of_data
        f.owner_email = ""
        f.owner_department = ""
        f.owner_resolved = True
        f.escalation_target = "DPO_or_data_governance_team"


def _assign_unresolved(findings: list[Finding]) -> None:
    for f in findings:
        f.assigned_owner = "DPO_or_data_governance_team"
        f.owner_email = ""
        f.owner_department = ""
        f.owner_resolved = False
        f.escalation_target = "DPO_or_data_governance_team"

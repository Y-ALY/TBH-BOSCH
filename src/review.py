"""Human review queue — create review items and process actions.

- create_review_queue(): build one ReviewItem per Finding.
- process_review_action(): record a decision on a review item.
- export_review_queue(): write the queue as JSON.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import (
    Finding,
    ReviewItem,
    ReviewAction,
    ALLOWED_REVIEW_ACTIONS,
)

# Which actions are available per finding type.
_TYPE_ACTIONS: dict[str, list[str]] = {
    "email":        ["retain", "mask", "delete", "false_positive", "escalate_dpo"],
    "employee_id":  ["retain", "mask", "delete", "false_positive", "escalate_dpo"],
    "tax_id":       ["delete", "archive", "false_positive", "escalate_dpo"],
    "address":      ["retain", "mask", "delete", "archive", "false_positive", "escalate_dpo"],
    "signature":    ["retain", "false_positive", "escalate_dpo"],
    "name":         ["retain", "mask", "delete", "false_positive", "escalate_dpo"],
}


def create_review_queue(findings: list[Finding]) -> list[ReviewItem]:
    """Create one ReviewItem for each finding.

    Review IDs are deterministic: derived from the finding ID so the same
    finding always maps to the same review ID across CLI invocations.
    """
    items: list[ReviewItem] = []
    for i, f in enumerate(findings, start=1):
        allowed = _TYPE_ACTIONS.get(f.type, list(ALLOWED_REVIEW_ACTIONS))
        # Use finding's own ID as the stem for a deterministic review ID
        stem = f.finding_id.replace("finding-", "")
        items.append(ReviewItem(
            review_id=f"review-{stem}",
            finding_id=f.finding_id,
            assigned_owner=f.assigned_owner,
            owner_status=_owner_status(f),
            allowed_actions=allowed,
        ))
    return items


def process_review_action(
    review_items: list[ReviewItem],
    review_id: str,
    action: str,
    reviewer: str,
    reason: str,
) -> ReviewItem | str:
    """Apply a human decision to a review item.

    Returns the updated ReviewItem, or an error string if the action
    is not allowed for this item.
    """
    item = next((r for r in review_items if r.review_id == review_id), None)
    if item is None:
        return f"Review item '{review_id}' not found."

    if action not in item.allowed_actions:
        return (
            f"Action '{action}' not allowed for review '{review_id}'. "
            f"Allowed: {item.allowed_actions}"
        )

    ra = ReviewAction(action=action, reviewer=reviewer, reason=reason)
    item.actions_log.append(ra)
    item.status = "completed"

    if action == "escalate_dpo":
        item.owner_status = "escalated"

    return item


def export_review_queue(
    review_items: list[ReviewItem],
    output_path: str,
) -> str:
    """Write review items as JSON to the given path. Returns the path."""
    from dataclasses import asdict

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "exported_at": datetime.now().isoformat(),
        "total_items": len(review_items),
        "pending": sum(1 for r in review_items if r.status == "pending"),
        "completed": sum(1 for r in review_items if r.status == "completed"),
        "items": [_review_item_to_dict(r) for r in review_items],
    }

    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return str(out)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _owner_status(f: Finding) -> str:
    if f.owner_resolved:
        return "resolved"
    if f.escalation_target:
        return "escalated"
    return "unresolved"


def _review_item_to_dict(item: ReviewItem) -> dict:
    return {
        "review_id": item.review_id,
        "finding_id": item.finding_id,
        "assigned_owner": item.assigned_owner,
        "owner_status": item.owner_status,
        "status": item.status,
        "allowed_actions": item.allowed_actions,
        "actions_log": [
            {
                "action": a.action,
                "reviewer": a.reviewer,
                "reason": a.reason,
                "timestamp": a.timestamp,
            }
            for a in item.actions_log
        ],
    }

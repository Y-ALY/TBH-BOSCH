"""Entity extraction and context classification.

- extract_entities(): regex-based PII / risk discovery.
- classify_context(): keyword-based document type detection.

Both are deliberately simple — designed to accept external regex/AI findings
later via the `external_findings` parameter.
"""

from __future__ import annotations

import re

from .models import Finding, PageContent

# ---------------------------------------------------------------------------
# Regex patterns — each maps to a finding type + risk level
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, str, str, str]] = [
    # (regex, type, risk_level, recommended_action)
    (
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        "email", "medium", "mask",
    ),
    (
        r'\bEMP-\d{5,8}\b',
        "employee_id", "medium", "mask",
    ),
    (
        r'\bDE\d{9}\b',
        "tax_id", "high", "delete",
    ),
    (
        r'\b\d{2}/\d{3}/\d{5}\b',
        "tax_id", "high", "delete",
    ),
    (
        r'\b\d{5}[ \t]+[A-Z][a-zäöüß]+([ \t]+\d{1,3}[a-z]?)?\b',
        "address", "medium", "review",
    ),
    (
        r'(?i)(?:Signed|Signature|Unterschrift)[\s:]*[\w\s.]+',
        "signature", "low", "retain",
    ),
    (
        r'(?im)^\s*(Name|Vorname|Nachname|Full Name|Employee|Manager|Participant|Teilnehmer|Reported by)\s*:\s*(.+)$',
        "name", "medium", "mask",
    ),
]

# ---------------------------------------------------------------------------
# Document type keywords
# ---------------------------------------------------------------------------

_TYPE_KEYWORDS: dict[str, list[str]] = {
    "expense_report": [
        "expense", "reimbursement", "receipt", "amount", "payment",
        "invoice", "travel", "hotel", "total", "date", "manager",
    ],
    "it_access_request": [
        "access", "permission", "system", "admin", "grant", "revoke",
        "credential", "username", "password", "authorized",
    ],
    "incident_report": [
        "incident", "accident", "injury", "hazard", "safety",
        "occurred", "reported", "witness",
    ],
    "supplier_onboarding": [
        "supplier", "vendor", "onboarding", "contract", "procurement",
        "vendor_code", "company", "tax id", "address", "contact",
    ],
    "training_evaluation": [
        "training", "evaluation", "course", "feedback", "instructor",
        "participant", "score", "rating", "completed",
    ],
}


_SEMANTIC_PATTERNS: list[tuple[str, str, str, str]] = [
    # (regex, type, risk_level, action)
    (r'(?i)\b(?:call|phone|contact(?:ed)?)\s+(?:[A-Z][a-z]+\s+)?(?:at|on)\s+([\d\-\+\(\)\s]{7,20})\b', "phone", "medium", "mask"),
    (r'(?i)\b(?:password|pwd|passcode|secret)\s*[:=]\s*([^\s]{5,20})\b', "password", "high", "mask"),
    (r'(?i)\b(?:ssn|social security)\s*[:=]?\s*([\d\-]{9,11})\b', "ssn", "high", "delete"),
]

def extract_entities(
    text: str,
    pages: list[PageContent],
    external_findings: list[Finding] | None = None,
) -> list[Finding]:
    """Scan text for PII/risk entities using a Two-Pass pipeline.

    Pass 1 (High-Speed Regex): Standard PII extraction.
    Pass 2 (Lightweight NLP/Semantic): Contextual checks on remaining text.

    Args:
        text: Full concatenated document text.
        pages: Page-by-page content (used for context extraction).
        external_findings: Optional findings from external classifiers (AI, etc.).
                           Merged into the result list.

    Returns:
        Flat list of Finding objects.
    """
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()  # (type, value) dedup

    # Build page text lookup for context extraction
    full_text = "\n".join(p.text for p in pages)
    
    # ── Pass 1: High-Speed Regex ──
    matched_ranges = [] # Store intervals of matched text

    for pattern, ftype, risk, action in _PATTERNS:
        for match in re.finditer(pattern, full_text, re.MULTILINE):
            value = match.group(0).strip()
            # Determine which group is the value (prefer capture groups)
            if match.lastindex and match.lastindex >= 2:
                value = match.group(match.lastindex).strip()

            if not value:
                continue

            matched_ranges.append((match.start(), match.end()))

            # Extract surrounding context (up to 80 chars)
            start_ctx = max(0, match.start() - 40)
            end_ctx = min(len(full_text), match.end() + 40)
            ctx = full_text[start_ctx:end_ctx].replace("\n", " ")

            # Determine which field name the value belongs to (from match groups)
            field_name = ""
            if match.lastindex and match.lastindex >= 1:
                field_name = match.group(1).strip().rstrip(":")

            # Dedup: skip if same (type, value) already found in this document
            dedup_key = (ftype, value)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            findings.append(Finding(
                finding_id="",
                file_id="",  # set by scanner
                type=ftype,
                value=value,
                field=field_name or ftype,
                context=ctx,
                risk_level=risk,
                confidence=1.0,
                evidence=f"Regex pattern matched: {pattern}",
                recommended_action=action,
                is_flagged=True,
                flag_type="Regex_Match"
            ))

    # ── Prepare for Pass 2: Mask out Regex Matches (O(N)) ──
    # Merge overlapping intervals to minimize masking work
    matched_ranges.sort()
    merged_ranges = []
    for r in matched_ranges:
        if not merged_ranges:
            merged_ranges.append(r)
        else:
            last = merged_ranges[-1]
            if r[0] <= last[1]:
                merged_ranges[-1] = (last[0], max(last[1], r[1]))
            else:
                merged_ranges.append(r)

    # Convert to list of chars to mask out ranges (O(N) operation)
    text_chars = list(full_text)
    for start_idx, end_idx in merged_ranges:
        for i in range(start_idx, end_idx):
            text_chars[i] = ' '
    
    remaining_text = "".join(text_chars)

    # ── Pass 2: Lightweight Semantic / Contextual Check ──
    # Runs ONLY on the text that wasn't already caught by standard Regex
    for pattern, ftype, risk, action in _SEMANTIC_PATTERNS:
        for match in re.finditer(pattern, remaining_text, re.MULTILINE):
            value = match.group(1).strip()
            if not value:
                continue
                
            start_ctx = max(0, match.start() - 40)
            end_ctx = min(len(remaining_text), match.end() + 40)
            ctx = remaining_text[start_ctx:end_ctx].replace("\n", " ")
            
            dedup_key = (ftype, value)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            findings.append(Finding(
                finding_id="",
                file_id="",
                type=ftype,
                value=value,
                field=ftype,
                context=ctx,
                risk_level=risk,
                confidence=0.8,
                evidence=f"Semantic context match",
                recommended_action=action,
                is_flagged=True,
                flag_type="Semantic_Match"
            ))

    # Merge external findings
    if external_findings:
        for f in external_findings:
            key = (f.type, f.value)
            if key not in seen:
                seen.add(key)
                findings.append(f)

    return findings


def classify_context(text: str, fields: dict) -> str:
    """Determine document type by counting keyword matches.

    Returns the type with the most keyword hits, or "unknown" if none.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}

    for doc_type, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[doc_type] = score

    if not scores:
        # Try field-name heuristic
        field_keys = " ".join(fields.keys()).lower()
        for doc_type, keywords in _TYPE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in field_keys)
            if score > 0:
                scores[doc_type] = score

    if not scores:
        return "unknown"

    return max(scores, key=lambda k: scores[k])

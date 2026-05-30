"""
fusion.py — Unified scanner merging teammate's PIIScanner with our German patterns.

The teammate (pii_filter) provides:
    • EMAIL, PHONE, IBAN, CREDIT_CARD detection
    • Luhn-check validation for credit cards
    • Overlap resolution (greedy interval scheduling)
    • Stateless, thread-safe PIIScanner class

We extend with German/GDPR-specific patterns:
    • tax_id (DE123456789, 00/000/00000 format)
    • employee_id (EMP-XXXXX)
    • address (German postal codes + city names)
    • signature blocks
    • name fields

And add our pipeline's capabilities:
    • context_classify() → document type
    • extract_fields() → structured key:value pairs
    • assign_owners() → responsibility chain

Usage:
    from src.fusion import UnifiedScanner, scan_document

    scanner = UnifiedScanner()
    result = scanner.scan("Contact: john.doe@bosch.com, Tax ID: DE123456789")
    # result.findings → merged PII matches
    # result.document_type → "supplier_onboarding" etc.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Teammate imports ──────────────────────────────────────────
from pii_filter.pii_scanner import PIIScanner, _luhn_check
from pii_filter.models import PIIType as TeammatePIIType, PIIMatch

# ── Our models (simplified, no circular deps) ─────────────────
from src.models import Finding, PageContent


# ======================================================================
# Extended PII types — teammate's 4 + our 5 German-specific
# ======================================================================

# Map our finding types to teammate's PIIType where they overlap
GERMAN_PII_TYPES = [
    "tax_id",
    "employee_id",
    "address",
    "signature",
    "name",
]

ALL_PII_TYPES = [t.value for t in TeammatePIIType] + GERMAN_PII_TYPES


# ======================================================================
# German regex patterns — compiled for teammate's scanner format
# ======================================================================

# Each pattern is (compiled_regex, "pii_type_string")
# We use strings instead of PIIType since we're adding custom types.

_GERMAN_TAX_DE = re.compile(r'\bDE\d{9}\b')
_GERMAN_TAX_SLASH = re.compile(r'\b\d{2}/\d{3}/\d{5}\b')
_GERMAN_EMPLOYEE_ID = re.compile(r'\bEMP-\d{5,8}\b')
_GERMAN_ADDRESS = re.compile(r'\b\d{5}[ \t]+[A-Z][a-zäöüß]+([ \t]+\d{1,3}[a-z]?)?\b')
_GERMAN_SIGNATURE = re.compile(r'(?i)(?:Signed|Signature|Unterschrift)[\s:]*[\w\s.]+')
_GERMAN_NAME_FIELD = re.compile(
    r'(?im)^\s*(Name|Vorname|Nachname|Full Name|Employee|Manager|Participant|Teilnehmer|Reported by)\s*:\s*(.+)$'
)


# ======================================================================
# Unified scanner
# ======================================================================

@dataclass
class UnifiedScanResult:
    """Complete scan output for a single document."""
    file_id: str
    file_name: str
    findings: list[dict] = field(default_factory=list)
    document_type: str = "unknown"
    fields: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    def by_type(self, pii_type: str) -> list[dict]:
        return [f for f in self.findings if f["type"] == pii_type]


class UnifiedScanner:
    """Combines teammate's PIIScanner with our German regex patterns.

    The teammate's scanner handles: EMAIL, PHONE, IBAN, CREDIT_CARD
    We add: tax_id, employee_id, address, signature, name

    All patterns benefit from the teammate's overlap resolution algorithm.
    """

    def __init__(self, enable_luhn: bool = True):
        # Base scanner (teammate's 4 types)
        self._base = PIIScanner(enable_luhn=enable_luhn)

        # Our German patterns — compiled regexes
        self._german_patterns: list[tuple[re.Pattern, str, str, str]] = [
            # (regex, type, risk_level, recommended_action)
            (_GERMAN_TAX_DE, "tax_id", "high", "delete"),
            (_GERMAN_TAX_SLASH, "tax_id", "high", "delete"),
            (_GERMAN_EMPLOYEE_ID, "employee_id", "medium", "mask"),
            (_GERMAN_ADDRESS, "address", "medium", "review"),
            (_GERMAN_SIGNATURE, "signature", "low", "retain"),
            (_GERMAN_NAME_FIELD, "name", "medium", "mask"),
        ]

    def scan(self, text: str, file_id: str = "", file_name: str = "") -> UnifiedScanResult:
        """Run all scanners on text and return unified results."""
        findings: list[dict] = []
        seen: set[tuple[str, str]] = set()

        # ── Step 1: Teammate's scanner (EMAIL, PHONE, IBAN, CC) ──
        pii_matches = self._base.scan(text)
        for m in pii_matches:
            key = (m.pii_type.value, m.matched_value)
            if key in seen:
                continue
            seen.add(key)
            findings.append(self._pii_match_to_finding(m, file_id))

        # ── Step 2: Our German patterns ──
        full_text = text
        for pattern, ptype, risk, action in self._german_patterns:
            for match in pattern.finditer(full_text):
                value = match.group(0).strip()
                # For name patterns, prefer the capture group (value after colon)
                if match.lastindex and match.lastindex >= 2:
                    value = match.group(match.lastindex).strip()

                if not value:
                    continue

                key = (ptype, value)
                if key in seen:
                    continue
                seen.add(key)

                # Context snippet
                start = max(0, match.start() - 40)
                end = min(len(full_text), match.end() + 40)
                ctx = full_text[start:end].replace("\n", " ")

                findings.append({
                    "finding_id": "",
                    "file_id": file_id,
                    "type": ptype,
                    "value": value,
                    "field": ptype,
                    "context": ctx,
                    "risk_level": risk,
                    "confidence": 1.0,
                    "evidence": f"Regex: {pattern.pattern[:60]}...",
                    "recommended_action": action,
                })

        # ── Step 3: Overlap dedup between teammate and German patterns ──
        # Teammate's scanner already deduplicates internally, but cross-system
        # overlaps can occur (e.g., a German address containing a phone-like number).
        # We use a simple span-based approach: sort by start position in the text,
        # and if two findings have overlapping values, keep the more specific one.
        findings = self._cross_system_dedup(findings, full_text)

        # ── Step 4: Context classification ──
        fields = _extract_fields(full_text)
        doc_type = _classify_context(full_text, fields)

        # ── Step 5: Stats ──
        type_counts: dict[str, int] = {}
        for f in findings:
            type_counts[f["type"]] = type_counts.get(f["type"], 0) + 1

        return UnifiedScanResult(
            file_id=file_id,
            file_name=file_name,
            findings=findings,
            document_type=doc_type,
            fields=fields,
            stats={
                "total_findings": len(findings),
                "by_type": type_counts,
                "document_type": doc_type,
            },
        )

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _pii_match_to_finding(m: PIIMatch, file_id: str) -> dict:
        """Convert teammate's PIIMatch to our Finding dict format."""
        risk_map = {
            "email": "medium",
            "phone": "medium",
            "iban": "high",
            "credit_card": "high",
        }
        action_map = {
            "email": "mask",
            "phone": "mask",
            "iban": "delete",
            "credit_card": "delete",
        }
        return {
            "finding_id": "",
            "file_id": file_id,
            "type": m.pii_type.value,
            "value": m.matched_value,
            "field": m.pii_type.value,
            "context": m.snippet,
            "risk_level": risk_map.get(m.pii_type.value, "medium"),
            "confidence": 1.0,
            "evidence": f"PIIScanner matched at offset {m.char_offset}",
            "recommended_action": action_map.get(m.pii_type.value, "review"),
        }

    @staticmethod
    def _cross_system_dedup(findings: list[dict], text: str) -> list[dict]:
        """Remove cross-system overlaps: keep the longer/more specific match."""
        if len(findings) <= 1:
            return findings

        # Enrich with span info by searching text
        enriched: list[tuple[int, int, dict]] = []
        for f in findings:
            val = f["value"]
            idx = text.find(val)
            if idx >= 0:
                enriched.append((idx, idx + len(val), f))
            else:
                enriched.append((0, 0, f))

        # Sort by start ASC, length DESC
        enriched.sort(key=lambda x: (x[0], -(x[1] - x[0])))

        result: list[dict] = []
        prev_end = -1
        for start, end, f in enriched:
            if start >= prev_end:
                result.append(f)
                prev_end = end

        return result


# ======================================================================
# Field extraction (from our scanner.py)
# ======================================================================

_FIELD_PATTERNS = [
    (r'(?im)^\s*(Company|Firma)\s*:\s*(.+)$', "Company"),
    (r'(?im)^\s*(Address|Adresse|Anschrift)\s*:\s*(.+)$', "Address"),
    (r'(?im)^\s*(Contact|Kontakt)\s*:\s*(.+)$', "Contact"),
    (r'(?im)^\s*(Tax ID|Steuer-ID|USt-IdNr\.?|VAT)\s*:\s*(.+)$', "Tax ID"),
    (r'(?im)^\s*(Employee|Mitarbeiter|Name)\s*:\s*(.+)$', "Employee"),
    (r'(?im)^\s*(Amount|Betrag|Summe|Total)\s*:\s*(.+)$', "Amount"),
    (r'(?im)^\s*(Date|Datum)\s*:\s*(.+)$', "Date"),
    (r'(?im)^\s*(Manager|Vorgesetzter)\s*:\s*(.+)$', "Manager"),
    (r'(?im)^\s*(System)\s*:\s*(.+)$', "System"),
    (r'(?im)^\s*(Access Level|Zugriffsstufe)\s*:\s*(.+)$', "Access Level"),
    (r'(?im)^\s*(Signature|Unterschrift)\s*:\s*(.+)$', "Signature"),
    (r'(?im)^\s*(Department|Abteilung)\s*:\s*(.+)$', "Department"),
    (r'(?im)^\s*(Email|E-Mail)\s*:\s*(.+)$', "Email"),
    (r'(?im)^\s*(Phone|Telefon|Tel\.?)\s*:\s*(.+)$', "Phone"),
    (r'(?im)^\s*(Participant|Teilnehmer)\s*:\s*(.+)$', "Participant"),
    (r'(?im)^\s*(Course|Kurs|Training)\s*:\s*(.+)$', "Course"),
    (r'(?im)^\s*(Score|Punktzahl|Rating|Bewertung)\s*:\s*(.+)$', "Score"),
]


def _extract_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for pattern, label in _FIELD_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = match.group(match.lastindex or 2).strip()
            if value and label not in fields:
                fields[label] = value
    return fields


# ======================================================================
# Context classification (from our classifier.py)
# ======================================================================

_TYPE_KEYWORDS: dict[str, list[str]] = {
    "expense_report": [
        "expense", "reimbursement", "receipt", "amount", "payment",
        "invoice", "travel", "hotel", "total",
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


def _classify_context(text: str, fields: dict) -> str:
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for doc_type, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[doc_type] = score
    if not scores:
        field_keys = " ".join(fields.keys()).lower()
        for doc_type, keywords in _TYPE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in field_keys)
            if score > 0:
                scores[doc_type] = score
    if not scores:
        return "unknown"
    return max(scores, key=lambda k: scores[k])


# ======================================================================
# Accuracy test corpus
# ======================================================================

# Each entry: (text, expected_types, expected_doc_type)
# "expected_types" is a dict of {type: min_expected_count}
GROUND_TRUTH = [
    # ── German corporate documents ──
    (
        "Employee: Anna Schmidt\nEmail: anna.schmidt@bosch.com\n"
        "Tax ID: DE123456789\nExpense Report\nAmount: 1,245.50 EUR\n"
        "Date: 2026-03-15\nManager: Dr. Hans Mueller\n"
        "Hotel receipt for travel to Munich",
        {"email": 1, "tax_id": 1, "name": 1},
        "expense_report",
    ),
    (
        "Supplier Onboarding Form\nCompany: Nordic Components GmbH\n"
        "Address: Hauptstr. 12, 70173 Stuttgart\n"
        "Contact: procurement@nordic-components.example\n"
        "Tax ID: DE987654321\nVendor Code: NC-2024-001\n"
        "Signed: Markus Weber",
        {"email": 1, "tax_id": 1, "address": 1, "signature": 1},
        "supplier_onboarding",
    ),
    (
        "IT Access Request\nName: Thomas Berger\n"
        "Employee ID: EMP-88421\nSystem: SAP Finance\n"
        "Access Level: Read-Write Admin\n"
        "Authorized by: Dr. Hans Mueller\n"
        "Signature: Thomas Berger",
        {"employee_id": 1, "name": 1, "signature": 1},
        "it_access_request",
    ),
    (
        "Safety Incident Report\nIncident ID: INC-2026-042\n"
        "Date of Incident: 2026-04-10\nReported by: Klaus Fischer\n"
        "Department: Manufacturing\nLocation: Building C\n"
        "Injury: Minor bruising\nWitness: Petra Wagner\n"
        "Hazard identified: Water leak",
        {},
        "incident_report",
    ),
    (
        "Training Evaluation\nCourse: GDPR Compliance Basics 2026\n"
        "Instructor: Prof. Julia Schmidt\n"
        "Participant: Lisa Hoffmann\n"
        "Employee ID: EMP-99123\nScore: 92/100\n"
        "Signature: Lisa Hoffmann",
        {"employee_id": 1, "signature": 1, "name": 1},
        "training_evaluation",
    ),

    # ── Financial / international PII (teammate's domain) ──
    (
        "Payment details: IBAN DE89 3704 0044 0532 0130 00\n"
        "Credit Card: 4539 1488 0343 6467\n"
        "Phone: +49 170 1234567",
        {"iban": 1, "credit_card": 1, "phone": 1},
        "unknown",
    ),
    (
        "Contact: hello@example.com\n"
        "Mobile: +49 711 400 40990\n"
        "Office: (030) 18681-0",
        {"email": 1, "phone": 2},
        "unknown",
    ),

    # ── Clean documents (should have 0 findings) ──
    (
        "MEMO: Q2 All-Hands Meeting\nDate: June 15, 2026\n"
        "Location: Building 7, Conference Room A\n"
        "Agenda: Revenue update, product roadmap, team awards.",
        {},
        "unknown",
    ),

    # ── Edge cases ──
    (
        "Error code AB12 occurred at node 7.\n"
        "Order number: 4539-1488-0343-6460\n"  # fails Luhn — should NOT flag as CC
        "Reference: DE12 3456 7890 1234 5678 00",  # IBAN-like but not real
        {"iban": 1},  # IBAN regex should still match
        "unknown",
    ),
    (
        "Use @mentions in Slack.\n"  # should NOT flag as email
        "Tax ID: 12/345/67890\n"     # German tax ID slash format
        "Employee: EMP-12345",       # Employee ID
        {"tax_id": 1, "employee_id": 1},
        "unknown",
    ),
]


def run_accuracy_test(scanner: UnifiedScanner | None = None) -> dict:
    """Run the scanner against ground truth and return accuracy metrics.

    Returns:
        {
            "total_docs": int,
            "correct_doc_types": int,
            "doc_type_accuracy": float,
            "precision": float,       # TP / (TP + FP)
            "recall": float,          # TP / (TP + FN)
            "f1": float,
            "per_type": {type: {"tp": int, "fp": int, "fn": int, "precision": float, "recall": float}},
            "false_positives": [...],
            "false_negatives": [...],
        }
    """
    if scanner is None:
        scanner = UnifiedScanner()

    total_tp = 0  # true positives
    total_fp = 0  # false positives
    total_fn = 0  # false negatives
    correct_doc_types = 0
    per_type: dict[str, dict] = {}
    false_positives: list[dict] = []
    false_negatives: list[dict] = []

    for text, expected_types, expected_doc_type in GROUND_TRUTH:
        result = scanner.scan(text, file_id="test", file_name="test.txt")

        # ── Document type accuracy ──
        if result.document_type == expected_doc_type:
            correct_doc_types += 1

        # ── PII type accuracy ──
        found_types: dict[str, int] = {}
        for f in result.findings:
            t = f["type"]
            found_types[t] = found_types.get(t, 0) + 1

        all_types = set(list(expected_types.keys()) + list(found_types.keys()))

        for ptype in all_types:
            expected = expected_types.get(ptype, 0)
            found = found_types.get(ptype, 0)

            if ptype not in per_type:
                per_type[ptype] = {"tp": 0, "fp": 0, "fn": 0}

            # True positives = min(expected, found)
            tp = min(expected, found)
            fp = max(0, found - expected)
            fn = max(0, expected - found)

            per_type[ptype]["tp"] += tp
            per_type[ptype]["fp"] += fp
            per_type[ptype]["fn"] += fn
            total_tp += tp
            total_fp += fp
            total_fn += fn

            # Record details for mismatches
            if fp > 0:
                extra = [f for f in result.findings if f["type"] == ptype][:fp]
                for e in extra:
                    false_positives.append({
                        "type": ptype,
                        "value": e["value"],
                        "text_snippet": text[:80],
                    })
            if fn > 0:
                false_negatives.append({
                    "type": ptype,
                    "expected": expected,
                    "found": found,
                    "text_snippet": text[:80],
                })

    # ── Compute metrics ──
    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    for pt, m in per_type.items():
        denom_p = m["tp"] + m["fp"]
        denom_r = m["tp"] + m["fn"]
        m["precision"] = m["tp"] / denom_p if denom_p > 0 else 0.0
        m["recall"] = m["tp"] / denom_r if denom_r > 0 else 0.0

    return {
        "total_docs": len(GROUND_TRUTH),
        "correct_doc_types": correct_doc_types,
        "doc_type_accuracy": correct_doc_types / len(GROUND_TRUTH),
        "total_tp": total_tp,
        "total_fp": total_fp,
        "total_fn": total_fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "per_type": per_type,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }

"""Core AI Parser — the central orchestrator.

Reads scanner/regex findings, sends them to AI (live or mock), and returns
structured GDPR classifications for the dashboard and review queue.

Modes:
- mock:  Uses local heuristics — no API call, no cost.  Good for demos and tests.
- live:  Calls OpenRouter with real prompts.  Requires OPENROUTER_API_KEY.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from .openrouter_client import OpenRouterClient
from .schemas import (
    BUSINESS_CONTEXT,
    CLASSIFICATION_STATUS,
    ClassificationResult,
    DATA_SUBJECT_TYPE,
    GDPR_DATA_TYPE,
    RECOMMENDED_ACTION,
    RISK_LEVEL,
    ScannerFinding,
)

logger = logging.getLogger(__name__)


class AIParser:
    """GDPR classification engine that enriches scanner findings with AI metadata."""

    def __init__(
        self,
        mode: str | None = None,
        openrouter_client: OpenRouterClient | None = None,
    ) -> None:
        self.mode: str = mode or os.getenv("AI_PARSER_MODE", "mock")
        if self.mode not in ("mock", "live"):
            raise ValueError(f"AI_PARSER_MODE must be 'mock' or 'live', got '{self.mode}'")

        self._openrouter = openrouter_client or OpenRouterClient()
        logger.info("AIParser initialized in '%s' mode", self.mode)

    # ── Public API ──────────────────────────────────────────────────────────

    def classify(self, finding: ScannerFinding | dict[str, Any]) -> ClassificationResult:
        """Classify a single scanner finding.

        Args:
            finding: A ScannerFinding model or a plain dict matching its shape.

        Returns:
            ClassificationResult with AI-enriched metadata.
        """
        if isinstance(finding, dict):
            finding = ScannerFinding(**finding)

        if self.mode == "mock":
            return self._classify_mock(finding)
        else:
            return self._classify_live(finding)

    def classify_batch(self, findings: list[ScannerFinding | dict[str, Any]]) -> list[ClassificationResult]:
        """Classify a batch of findings.  Each is processed independently."""
        results: list[ClassificationResult] = []
        for f in findings:
            try:
                results.append(self.classify(f))
            except Exception:
                logger.exception("Failed to classify finding: %s", f)
                results.append(self._emergency_fallback(f))
        return results

    # ── Mock mode ───────────────────────────────────────────────────────────

    def _classify_mock(self, f: ScannerFinding) -> ClassificationResult:
        """Heuristic-based classification — no API call, zero cost.

        Strategy (order matters):
        1. Exact-match against the 4 canonical test-case snippets.
        2. Regex-type-based classification for common patterns.
        3. Snippet-content-based fallback heuristics.
        """
        snippet_lower = f.snippet.lower()

        # ── 1. Canonical test cases (exact or substring match) ──────────

        # Test case 1: Employee ID with name
        if "sara hoffmann" in snippet_lower or (
            f.regex_type == "employee_id" and "employee" in snippet_lower.lower()
        ):
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="employee_identifier",
                data_subject_type="employee",
                business_context="finance_travel_reimbursement",
                risk_level="medium",
                confidence=0.92,
                recommended_action="human_review",
                explanation=(
                    "The employee ID is linked to a named employee in an expense "
                    "reimbursement document, which makes it GDPR-relevant personal data."
                ),
                dashboard_label="Employee expense record containing employee identifier",
                status="mock",
            )

        # Test case 2: Business contact email (supplier)
        if "procurement@nordic-components" in snippet_lower or (
            f.regex_type == "email"
            and ("procurement" in snippet_lower or "contact" in snippet_lower.lower())
        ):
            return self._make_result(
                f,
                is_personal_data=False,
                gdpr_data_type="business_contact",
                data_subject_type="supplier",
                business_context="supplier_contact",
                risk_level="low",
                confidence=0.88,
                recommended_action="ignore",
                explanation=(
                    "This is a generic business procurement email address, not linked "
                    "to an identifiable individual. Not GDPR-relevant personal data."
                ),
                dashboard_label="Supplier business contact — not personal data",
                status="mock",
            )

        # Test case 3: Signature / name initial
        if "j. keller" in snippet_lower or (
            f.field.lower() == "signature" and f.regex_type in ("name", "signature", "name_initial")
        ):
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="name_initial",
                data_subject_type="employee",
                business_context="access_approval",
                risk_level="medium",
                confidence=0.85,
                recommended_action="human_review",
                explanation=(
                    "The signature contains a name initial ('J. Keller') which directly "
                    "identifies an individual. Moderate GDPR risk in an access approval context."
                ),
                dashboard_label="Signature with name initial — personal data",
                status="mock",
            )

        # Test case 4: Incident / data breach mention
        if "mistakenly shared" in snippet_lower or "personal data was" in snippet_lower:
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="personal_description",
                data_subject_type="unknown",
                business_context="incident_data_breach",
                risk_level="high",
                confidence=0.95,
                recommended_action="escalate",
                explanation=(
                    "The snippet describes a data-sharing incident involving personal data. "
                    "This requires immediate review as potential GDPR Article 33/34 notification trigger."
                ),
                dashboard_label="⚠️ Data breach incident mention — escalate immediately",
                status="mock",
            )

        # ── 2. Regex-type-based heuristics ──────────────────────────────

        result = self._classify_by_regex_type(f)
        if result:
            return result

        # ── 3. Snippet-content fallback ─────────────────────────────────

        return self._classify_by_snippet_content(f)

    def _classify_by_regex_type(self, f: ScannerFinding) -> ClassificationResult | None:
        """Return a classification based on regex_type, or None to fall through."""
        snippet = f.snippet
        val = f.regex_value

        if f.regex_type == "email":
            # Personal email (gmail, outlook, etc.) vs corporate
            if re.search(r"@(gmail|yahoo|outlook|hotmail|proton)\.", val, re.IGNORECASE):
                return self._make_result(
                    f,
                    is_personal_data=True,
                    gdpr_data_type="email_address",
                    data_subject_type="unknown",
                    business_context="general_correspondence",
                    risk_level="medium",
                    confidence=0.90,
                    recommended_action="human_review",
                    explanation="Personal email address — likely identifies an individual.",
                    dashboard_label="Personal email address",
                    status="mock",
                )
            # Corporate / business email
            return self._make_result(
                f,
                is_personal_data=False,
                gdpr_data_type="business_contact",
                data_subject_type="supplier",
                business_context="supplier_contact",
                risk_level="low",
                confidence=0.85,
                recommended_action="ignore",
                explanation="Corporate email address — likely a business contact, not personal data.",
                dashboard_label="Corporate email — not personal data",
                status="mock",
            )

        if f.regex_type == "phone":
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="phone_number",
                data_subject_type="unknown",
                business_context="general_correspondence",
                risk_level="medium",
                confidence=0.88,
                recommended_action="human_review",
                explanation="Phone number found — may identify an individual.",
                dashboard_label="Phone number",
                status="mock",
            )

        if f.regex_type == "iban":
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="iban",
                data_subject_type="unknown",
                business_context="finance_general",
                risk_level="high",
                confidence=0.90,
                recommended_action="human_review",
                explanation="IBAN found — financial personal data under GDPR.",
                dashboard_label="IBAN (bank account number)",
                status="mock",
            )

        if f.regex_type == "credit_card":
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="credit_card_number",
                data_subject_type="unknown",
                business_context="finance_general",
                risk_level="high",
                confidence=0.92,
                recommended_action="escalate",
                explanation="Credit card number — high-risk financial personal data.",
                dashboard_label="Credit card number — escalate",
                status="mock",
            )

        if f.regex_type in ("tax_id", "tax_identifier"):
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="tax_identifier",
                data_subject_type="unknown",
                business_context="finance_general",
                risk_level="high",
                confidence=0.88,
                recommended_action="human_review",
                explanation="Tax identifier — sensitive financial personal data.",
                dashboard_label="Tax identifier",
                status="mock",
            )

        if f.regex_type in ("employee_id",):
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="employee_identifier",
                data_subject_type="employee",
                business_context="hr_personnel_record",
                risk_level="medium",
                confidence=0.87,
                recommended_action="human_review",
                explanation="Employee ID — identifies an individual employee.",
                dashboard_label="Employee identifier",
                status="mock",
            )

        if f.regex_type in ("name", "full_name", "name_initial"):
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="full_name" if f.regex_type == "full_name" else "name_initial",
                data_subject_type="unknown",
                business_context="unknown",
                risk_level="medium",
                confidence=0.82,
                recommended_action="human_review",
                explanation="Name or name initial found — likely identifies an individual.",
                dashboard_label="Personal name",
                status="mock",
            )

        if f.regex_type in ("address",):
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="address",
                data_subject_type="unknown",
                business_context="unknown",
                risk_level="medium",
                confidence=0.80,
                recommended_action="human_review",
                explanation="Physical address — may identify an individual's residence.",
                dashboard_label="Physical address",
                status="mock",
            )

        if f.regex_type == "signature":
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="signature",
                data_subject_type="unknown",
                business_context="unknown",
                risk_level="medium",
                confidence=0.83,
                recommended_action="human_review",
                explanation="Signature found — directly identifies an individual.",
                dashboard_label="Signature",
                status="mock",
            )

        return None  # fall through to snippet-content heuristics

    def _classify_by_snippet_content(self, f: ScannerFinding) -> ClassificationResult:
        """Last-resort classification based on keyword scanning of the snippet text."""
        snippet_lower = f.snippet.lower()

        # Incident / breach keywords
        incident_keywords = [
            "breach", "leak", "mistakenly shared", "unauthorized",
            "data incident", "gdpr violation", "personal data was",
        ]
        if any(kw in snippet_lower for kw in incident_keywords):
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="personal_description",
                data_subject_type="unknown",
                business_context="incident_data_breach",
                risk_level="high",
                confidence=0.78,
                recommended_action="escalate",
                explanation="Snippet references a data incident or potential breach.",
                dashboard_label="⚠️ Potential data incident",
                status="mock",
            )

        # HR / personnel keywords
        hr_keywords = ["employee", "staff", "personnel", "salary", "contract", "hired", "termination"]
        if any(kw in snippet_lower for kw in hr_keywords):
            return self._make_result(
                f,
                is_personal_data=True,
                gdpr_data_type="other_personal_data",
                data_subject_type="employee",
                business_context="hr_personnel_record",
                risk_level="medium",
                confidence=0.70,
                recommended_action="human_review",
                explanation="Snippet appears to reference employee/personnel information.",
                dashboard_label="Potential HR/personnel data",
                status="mock",
            )

        # Finance keywords
        finance_keywords = ["invoice", "payment", "expense", "reimbursement", "bank", "salary"]
        if any(kw in snippet_lower for kw in finance_keywords):
            return self._make_result(
                f,
                is_personal_data=False,
                gdpr_data_type="not_personal_data",
                data_subject_type="unknown",
                business_context="finance_general",
                risk_level="low",
                confidence=0.65,
                recommended_action="ignore",
                explanation="Financial context but no clear personal data detected.",
                dashboard_label="Financial document — no clear PII",
                status="mock",
            )

        # Default: low-confidence "needs review"
        return self._make_result(
            f,
            is_personal_data=False,
            gdpr_data_type="not_personal_data",
            data_subject_type="unknown",
            business_context="unknown",
            risk_level="low",
            confidence=0.50,
            recommended_action="human_review",
            explanation="Insufficient signal to classify automatically. Human review recommended.",
            dashboard_label="Uncertain — needs human review",
            status="mock",
        )

    # ── Live mode ───────────────────────────────────────────────────────────

    def _classify_live(self, f: ScannerFinding) -> ClassificationResult:
        """Send the finding to OpenRouter for real AI classification."""
        finding_dict = {
            "file_id": f.file_id,
            "file_name": f.file_name,
            "document_type": f.document_type,
            "regex_type": f.regex_type,
            "regex_value": f.regex_value,
            "field": f.field,
            "snippet": f.snippet,
        }

        ai_result = self._openrouter.classify(finding_dict)
        return ClassificationResult(
            file_id=f.file_id,
            file_name=f.file_name,
            is_personal_data=ai_result.get("is_personal_data", False),
            gdpr_data_type=ai_result.get("gdpr_data_type", "not_personal_data"),
            data_subject_type=ai_result.get("data_subject_type", "unknown"),
            business_context=ai_result.get("business_context", "unknown"),
            risk_level=ai_result.get("risk_level", "low"),
            confidence=ai_result.get("confidence", 0.0),
            recommended_action=ai_result.get("recommended_action", "ignore"),
            explanation=ai_result.get("explanation", ""),
            dashboard_label=ai_result.get("dashboard_label", ""),
            classification_status=ai_result.get("classification_status", "ai_failed"),
            original_regex_type=f.regex_type,
            original_regex_value=f.regex_value,
            original_snippet=f.snippet,
        )

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _make_result(
        f: ScannerFinding,
        *,
        is_personal_data: bool,
        gdpr_data_type: GDPR_DATA_TYPE,
        data_subject_type: DATA_SUBJECT_TYPE,
        business_context: BUSINESS_CONTEXT,
        risk_level: RISK_LEVEL,
        confidence: float,
        recommended_action: RECOMMENDED_ACTION,
        explanation: str,
        dashboard_label: str,
        status: CLASSIFICATION_STATUS,
    ) -> ClassificationResult:
        return ClassificationResult(
            file_id=f.file_id,
            file_name=f.file_name,
            is_personal_data=is_personal_data,
            gdpr_data_type=gdpr_data_type,
            data_subject_type=data_subject_type,
            business_context=business_context,
            risk_level=risk_level,
            confidence=confidence,
            recommended_action=recommended_action,
            explanation=explanation,
            dashboard_label=dashboard_label,
            classification_status=status,
            original_regex_type=f.regex_type,
            original_regex_value=f.regex_value,
            original_snippet=f.snippet,
        )

    @staticmethod
    def _emergency_fallback(finding: ScannerFinding | dict[str, Any]) -> ClassificationResult:
        """Absolute last-resort fallback when even mock classification crashes."""
        if isinstance(finding, dict):
            return ClassificationResult(
                file_id=finding.get("file_id", ""),
                file_name=finding.get("file_name", ""),
                is_personal_data=False,
                gdpr_data_type="not_personal_data",
                classification_status="ai_failed",
                explanation="Emergency fallback — classification crashed.",
            )
        return ClassificationResult(
            file_id=finding.file_id,
            file_name=finding.file_name,
            is_personal_data=False,
            gdpr_data_type="not_personal_data",
            classification_status="ai_failed",
            explanation="Emergency fallback — classification crashed.",
        )

"""OpenRouter API client for GDPR data classification.

Handles authentication, request construction, retry logic, and response parsing.
The API key lives in backend environment variables only — never exposed to the frontend.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are a GDPR data classification engine.
You analyze enterprise document snippets from scanned corporate files.
You do not modify files.
Classify whether the snippet contains GDPR-relevant personal data.

Return ONLY valid JSON with these exact fields:
- is_personal_data (boolean): true if the snippet contains personal data under GDPR
- gdpr_data_type (string): one of [employee_identifier, email_address, phone_number, iban, credit_card_number, tax_identifier, name_initial, full_name, signature, address, business_contact, personal_description, other_personal_data, not_personal_data]
- data_subject_type (string): one of [employee, supplier, customer, manager, unknown]
- business_context (string): one of [hr_personnel_record, hr_payroll, hr_training, finance_travel_reimbursement, finance_invoice, finance_general, access_approval, access_control, incident_report, incident_data_breach, supplier_contract, supplier_contact, training_material, general_correspondence, unknown]
- risk_level (string): one of [low, medium, high]
- confidence (float): between 0.0 and 1.0
- recommended_action (string): one of [human_review, ignore, escalate, auto_approve]
- explanation (string): brief explanation of the classification
- dashboard_label (string): short label for dashboard display

Follow GDPR definitions strictly:
- Personal data = any information relating to an identified or identifiable natural person
- High risk = sensitive data (health, biometrics, criminal records) or data breach incident mentions
- Medium risk = employee IDs, names in business docs, signatures
- Low risk = generic business contacts, non-personal references"""

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2.0


# ── Client ─────────────────────────────────────────────────────────────────

class OpenRouterClient:
    """Thin wrapper around OpenRouter's chat completions API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.model = model or os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-safeguard-20b")

        if not self.api_key:
            logger.warning(
                "OPENROUTER_API_KEY is not set. "
                "Live mode will fail. Use mock mode for development."
            )

    # ── Public API ──────────────────────────────────────────────────────

    def classify(self, finding: dict[str, Any]) -> dict[str, Any]:
        """Send a single scanner finding to OpenRouter and return the classification JSON.

        Args:
            finding: Dict with file_name, document_type, regex_type, regex_value, field, snippet.

        Returns:
            Parsed classification dict matching ClassificationResult fields.
            On failure, returns a dict with classification_status="ai_failed".
        """
        user_prompt = self._build_user_prompt(finding)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._send_request(user_prompt)
                parsed = self._parse_response(response)
                parsed["classification_status"] = "success"
                return parsed
            except OpenRouterError as exc:
                logger.error("OpenRouter attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES:
                    wait = RETRY_DELAY_SECONDS * attempt
                    logger.info("Retrying in %.1fs...", wait)
                    time.sleep(wait)
            except Exception:
                logger.exception("Unexpected error on attempt %d/%d", attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)

        return self._fallback_result(finding)

    # ── Private helpers ─────────────────────────────────────────────────

    def _send_request(self, user_prompt: str) -> dict[str, Any]:
        """Send the chat completion request. Raises OpenRouterError on failure."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 500,
        }

        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        except requests.RequestException as exc:
            raise OpenRouterError(f"HTTP request failed: {exc}") from exc

        if resp.status_code == 401:
            raise OpenRouterError("Invalid or missing API key (401 Unauthorized)")
        if resp.status_code == 429:
            raise OpenRouterError("Rate limited (429). Consider adding delay or upgrading plan.")
        if resp.status_code != 200:
            raise OpenRouterError(f"OpenRouter returned {resp.status_code}: {resp.text[:300]}")

        try:
            return resp.json()
        except ValueError as exc:
            raise OpenRouterError("Failed to parse OpenRouter response as JSON") from exc

    @staticmethod
    def _parse_response(response: dict[str, Any]) -> dict[str, Any]:
        """Extract and parse the AI's JSON from the OpenRouter response."""
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError("Unexpected OpenRouter response structure") from exc

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            # Remove opening fence (```json or ```)
            content = content.split("\n", 1)[-1] if "\n" in content else content[3:]
            # Remove closing fence
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenRouterError(f"AI response was not valid JSON: {content[:200]}") from exc

    @staticmethod
    def _build_user_prompt(finding: dict[str, Any]) -> str:
        """Build the user prompt for a single finding."""
        return (
            f"File name: {finding.get('file_name', 'unknown')}\n"
            f"Document type: {finding.get('document_type', 'unknown')}\n"
            f"Regex type: {finding.get('regex_type', 'unknown')}\n"
            f"Regex value: {finding.get('regex_value', 'unknown')}\n"
            f"Field: {finding.get('field', '')}\n"
            f"Snippet: {finding.get('snippet', '')}\n"
            f"\nTask: Classify the finding and explain why it should or should not require human review."
        )

    @staticmethod
    def _fallback_result(finding: dict[str, Any]) -> dict[str, Any]:
        """Return a safe fallback when the AI API is unavailable."""
        return {
            "file_id": finding.get("file_id", ""),
            "file_name": finding.get("file_name", ""),
            "is_personal_data": False,
            "gdpr_data_type": "not_personal_data",
            "data_subject_type": "unknown",
            "business_context": "unknown",
            "risk_level": "low",
            "confidence": 0.0,
            "recommended_action": "ignore",
            "explanation": "",
            "dashboard_label": "",
            "classification_status": "ai_failed",
            "original_regex_type": finding.get("regex_type", ""),
            "original_regex_value": finding.get("regex_value", ""),
            "original_snippet": finding.get("snippet", ""),
        }


# ── Exceptions ─────────────────────────────────────────────────────────────

class OpenRouterError(Exception):
    """Raised when the OpenRouter API call fails."""

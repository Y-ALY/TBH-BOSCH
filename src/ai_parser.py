"""AI-powered document parsing via OpenRouter API.

Replaces/extends regex-based classification with LLM analysis:
  1. Document type classification (5 business categories)
  2. Entity extraction with risk assessment
  3. Context-aware PII detection (things regex can't catch)

Usage:
    export OPENROUTER_API_KEY="sk-or-..."

    from src.ai_parser import AIParser
    parser = AIParser()
    result = parser.parse(full_text, pages)
    # result.document_type, result.findings, result.summary
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Load .env file if it exists (no extra dependency needed)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

# OpenAI-compatible client (OpenRouter speaks this protocol)
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


# ======================================================================
# Prompts
# ======================================================================

SYSTEM_PROMPT = """Role: You are an AI data discovery and compliance automation agent. Analyze document text and return ONLY valid JSON.

## Your Task
Your objective is to identify personal data within unstructured files across corporate databases (OneDrives, SharePoint sites, and shared drives).
You must provide suggestions for data categorization and flag files for final review and deletion by a human to ensure compliance.
Do not automatically delete any files.

## Flagging Logic & Violation Rules
Do NOT flag a file as a violation solely because it contains personal data. Finding personal data is only the baseline. You must flag a file for human review and action only if it meets the baseline condition AND at least one of the specific violation conditions below:

Baseline Condition: The file contains one or more instances of targeted personal data.
Target Personal Data Types:
- First name, last name
- Username / login name
- Email address
- Signature
- Photo / video of a person
- Phone number (mobile / landline)
- Fax number
- Home address
- Billing / shipping address
- Passport number
- ID card number
- Driver’s license number
- Travel history

Violation Reason 1: Breach of Core Data Processing Principles (Article 5). E.g., Storage Limitation (storing personal data beyond the mandated 3-year retention period), Data Minimisation, Purpose Limitation, Accuracy.
Violation Reason 2: Ignoring Data Subject Rights (Articles 12-22). E.g., Right to Erasure / Right to be Forgotten (Article 17), Right of Access & Portability, Right to Object.
Violation Reason 3: Insufficient Data Security and Privacy by Design (Articles 25 & 32). E.g., Lack of Data Protection by Design, Security of Processing, Failure to Report Breaches.
Violation Reason 4: Processing Without a Lawful Basis or Invalid Consent (Article 6). E.g., Invalid Consent, No Lawful Ground.
Violation Reason 5: Unlawful Processing of Special Categories of Data (Article 9). E.g., Mishandling Sensitive Data like race, health, biometrics.
Violation Reason 6: Unlawful International Data Transfers (Articles 44-49). E.g., Unsafe Third-Country Transfers.
Violation Reason 7: Non-Compliance with Supervisory Authorities. E.g., Ignoring Directives.

## Document Types
- expense_report: travel claims, receipts, reimbursement forms
- it_access_request: system access, permission grants, admin requests
- incident_report: safety incidents, workplace accidents, hazard reports
- supplier_onboarding: vendor registration, procurement forms, contracts
- training_evaluation: course feedback, participant assessments, certification
- unknown: doesn't clearly match any above

## IMPORTANT RULES
- Only flag REAL violations based on the violation rules above. Do NOT flag simply because PII exists unless it violates a rule.
- `context` MUST explain WHY it's a GDPR risk (e.g. which violation reason it triggers and why).
- `recommended_action`: retain | mask | delete | archive | false_positive | escalate_dpo
- If text is empty or contains no violations, return an empty findings array.
- You must attribute the discovered data to a responsible person, either as a direct owner (for OneDrive) or indirectly as a "Master of Data" (for SharePoint and Shared Drives). Output this in `responsible_person`.
- The `violation_reason` field MUST be included in the finding if it is flagged.

## Output Format (strict JSON, no markdown, no explanation)
{
  "document_type": "expense_report",
  "confidence": 0.95,
  "summary": "One-line document description",
  "flags": ["finance", "personal_data"],
  "findings": [
    {
      "type": "email",
      "value": "john.doe@bosch.com",
      "field": "Email",
      "risk_level": "medium",
      "confidence": 0.98,
      "context": "Email address found in an expense report from 2018. Exceeds the 3-year retention period.",
      "recommended_action": "delete",
      "violation_reason": "Violation Reason 1: Breach of Core Data Processing Principles (Article 5)",
      "responsible_person": "Anna Schmidt"
    }
  ]
}"""


USER_PROMPT_TEMPLATE = """## Document Fields Extracted
{fields_json}

## Document Text (first 3000 chars)
{text}

## Page Count: {page_count}
## Existing Regex Findings: {regex_count} hits

Analyze this document and return the JSON."""


# ======================================================================
# Result model
# ======================================================================

@dataclass
class AIParseResult:
    document_type: str = "unknown"
    confidence: float = 0.0
    summary: str = ""
    flags: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    raw_response: str = ""
    model_used: str = ""
    tokens_used: int = 0


# ======================================================================
# Parser
# ======================================================================

class AIParser:
    """Send document text to OpenRouter LLM for GDPR analysis.

    Requires OPENROUTER_API_KEY env var.
    Optional: OPENROUTER_MODEL (default: openai/gpt-oss-safeguard-20b)
    """

    DEFAULT_MODEL = "openai/gpt-oss-safeguard-20b"
    API_BASE = "https://openrouter.ai/api/v1"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        if OpenAI is None:
            raise ImportError("pip install openai (OpenRouter uses OpenAI-compatible API)")

        self.model = model or os.getenv("OPENROUTER_MODEL", self.DEFAULT_MODEL)
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "Set OPENROUTER_API_KEY env var or pass api_key= to AIParser()"
            )

        self._client = OpenAI(
            base_url=self.API_BASE,
            api_key=self.api_key,
        )

    # ── Public API ─────────────────────────────────────────────

    def parse(
        self,
        text: str,
        fields: dict | None = None,
        page_count: int = 1,
        regex_findings_count: int = 0,
    ) -> AIParseResult:
        """Analyze document text and return structured GDPR findings."""
        if not text or not text.strip():
            return AIParseResult()

        # Truncate to avoid token limits (most models handle ~4K input fine)
        text_slice = text[:3000]
        fields_json = json.dumps(fields or {}, ensure_ascii=False, indent=2)

        user_prompt = USER_PROMPT_TEMPLATE.format(
            fields_json=fields_json,
            text=text_slice,
            page_count=page_count,
            regex_count=regex_findings_count,
        )

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,  # low temp for consistent structured output
                max_tokens=2000,
                extra_headers={
                    "HTTP-Referer": "https://github.com/Y-ALY/TBH-BOSCH",
                    "X-Title": "GDPR Scanner Hackathon",
                },
            )
        except Exception as e:
            return AIParseResult(
                summary=f"API error: {e}",
                raw_response=str(e),
            )

        raw = response.choices[0].message.content or ""
        usage = response.usage

        # Parse JSON from response (handle markdown-wrapped JSON)
        parsed = self._extract_json(raw)

        return AIParseResult(
            document_type=parsed.get("document_type", "unknown"),
            confidence=float(parsed.get("confidence", 0)),
            summary=parsed.get("summary", ""),
            flags=parsed.get("flags", []),
            findings=parsed.get("findings", []),
            raw_response=raw,
            model_used=self.model,
            tokens_used=usage.total_tokens if usage else 0,
        )

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """Extract JSON object from LLM response (may be wrapped in markdown)."""
        # Try direct parse first
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` block
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } pair
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return {}


# ======================================================================
# Quick test
# ======================================================================

if __name__ == "__main__":
    import sys

    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        print("❌ Set OPENROUTER_API_KEY first")
        print("   export OPENROUTER_API_KEY='sk-or-...'")
        sys.exit(1)

    parser = AIParser()
    print(f"Model: {parser.model}\n")

    test_text = """Expense Report - March 2026
    Employee: Anna Schmidt
    Email: anna.schmidt@bosch.com
    Department: Engineering
    Date: 2026-03-15
    Amount: 1,245.50 EUR
    Manager: Dr. Hans Mueller
    Travel expense for client meeting in Munich
    Hotel receipt attached: 245.00 EUR"""

    result = parser.parse(
        text=test_text,
        fields={"Employee": "Anna Schmidt", "Amount": "1,245.50 EUR"},
        page_count=1,
    )

    print(f"Type: {result.document_type} (confidence={result.confidence})")
    print(f"Model: {result.model_used} | Tokens: {result.tokens_used}")
    print(f"Flags: {result.flags}")
    print(f"Summary: {result.summary}")
    print(f"Findings: {len(result.findings)}")
    for f in result.findings:
        print(f"  [{f.get('type')}] {f.get('value')} risk={f.get('risk_level')}")

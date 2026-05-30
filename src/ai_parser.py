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

SYSTEM_PROMPT = """You are a GDPR compliance auditor. Analyze document text and return ONLY valid JSON.

## Your Task
1. Classify the document into exactly ONE type
2. Extract personal/business PII entities with risk assessment
3. Identify GDPR-relevant context flags

## Document Types
- expense_report: travel claims, receipts, reimbursement forms
- it_access_request: system access, permission grants, admin requests
- incident_report: safety incidents, workplace accidents, hazard reports
- supplier_onboarding: vendor registration, procurement forms, contracts
- training_evaluation: course feedback, participant assessments, certification
- unknown: doesn't clearly match any above

## Entity Types to Extract
- email, phone, iban, credit_card (financial/contact)
- tax_id, employee_id (corporate identifiers)
- address, name, signature (personal data)
- other_pii (any other GDPR-relevant personal data)

## Risk Levels
- high: financial data, tax IDs, IBANs, credit cards, health data
- medium: emails, phone numbers, employee IDs, addresses
- low: names without context, signatures, department names

## IMPORTANT RULES
- Only flag REAL PII — don't flag company names or generic department names
- context MUST explain WHY it's a GDPR risk, not just what it is
- recommended_action: retain | mask | delete | archive | false_positive | escalate_dpo
- If text is empty or contains no PII, return empty findings array
- A signature on an official form is LOW risk. A signature on a medical report is HIGH risk.
- An address of a COMPANY is LOW risk. An address of an EMPLOYEE is HIGH risk.

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
      "context": "Employee email address in expense report header — identifies individual",
      "recommended_action": "mask"
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
    Optional: OPENROUTER_MODEL (default: anthropic/claude-sonnet-4)
    """

    DEFAULT_MODEL = "anthropic/claude-sonnet-4"
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

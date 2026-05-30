"""Pydantic data models for the AI Parsing Backend.

Defines the contract between the scanner/regex layer and the AI classification layer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- Input: what the scanner sends us ---

class ScannerFinding(BaseModel):
    """A single suspicious snippet flagged by the regex/scanner layer."""

    file_id: str = Field(..., description="Unique file identifier, e.g. 'local:Report.pdf'")
    file_name: str = Field(..., description="Human-readable file name")
    document_type: str = Field(
        default="unknown",
        description="Classified document type, e.g. expense_report, contract, incident_report",
    )
    page_number: int = Field(default=1, ge=1, description="Page number where the snippet was found")
    field: str = Field(default="", description="Document field name, e.g. 'Employee', 'Signature'")
    snippet: str = Field(..., description="The raw text snippet containing the potential PII")
    regex_type: str = Field(..., description="Regex category: employee_id, email, phone, iban, name, etc.")
    regex_value: str = Field(..., description="The exact regex match value")


# --- Classification enums ---

GDPR_DATA_TYPE = Literal[
    "employee_identifier",
    "email_address",
    "phone_number",
    "iban",
    "credit_card_number",
    "tax_identifier",
    "name_initial",
    "full_name",
    "signature",
    "address",
    "business_contact",
    "personal_description",
    "other_personal_data",
    "not_personal_data",
]

DATA_SUBJECT_TYPE = Literal[
    "employee",
    "supplier",
    "customer",
    "manager",
    "unknown",
]

BUSINESS_CONTEXT = Literal[
    "hr_personnel_record",
    "hr_payroll",
    "hr_training",
    "finance_travel_reimbursement",
    "finance_invoice",
    "finance_general",
    "access_approval",
    "access_control",
    "incident_report",
    "incident_data_breach",
    "supplier_contract",
    "supplier_contact",
    "training_material",
    "general_correspondence",
    "unknown",
]

RISK_LEVEL = Literal["low", "medium", "high"]

RECOMMENDED_ACTION = Literal["human_review", "ignore", "escalate", "auto_approve"]

CLASSIFICATION_STATUS = Literal["success", "ai_failed", "mock"]


# --- Output: what we return to the dashboard / database ---

class ClassificationResult(BaseModel):
    """AI-enriched classification of a single scanner finding."""

    file_id: str = Field(..., description="Same file_id as input")
    file_name: str = Field(..., description="Same file_name as input")

    # Core classification
    is_personal_data: bool = Field(..., description="Whether the snippet contains GDPR-relevant personal data")
    gdpr_data_type: GDPR_DATA_TYPE = Field(
        default="not_personal_data",
        description="GDPR category of the personal data",
    )
    data_subject_type: DATA_SUBJECT_TYPE = Field(
        default="unknown",
        description="Role of the person: employee, supplier, customer, manager, unknown",
    )
    business_context: BUSINESS_CONTEXT = Field(
        default="unknown",
        description="Business context: HR, finance, access, incident, supplier, training",
    )
    risk_level: RISK_LEVEL = Field(default="low", description="GDPR risk level")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="AI confidence score")

    # Action
    recommended_action: RECOMMENDED_ACTION = Field(
        default="ignore",
        description="Recommended next step for the review team",
    )

    # Human-readable
    explanation: str = Field(default="", description="Why this classification was made")
    dashboard_label: str = Field(default="", description="Short label for dashboard display")

    # Metadata
    classification_status: CLASSIFICATION_STATUS = Field(
        default="success",
        description="Whether classification succeeded, failed, or used mock mode",
    )

    # Preserve original scanner data for traceability
    original_regex_type: str = Field(default="", description="Preserved from input")
    original_regex_value: str = Field(default="", description="Preserved from input")
    original_snippet: str = Field(default="", description="Preserved from input")


class BatchClassificationResult(BaseModel):
    """Wrapper for batch processing results."""

    results: list[ClassificationResult] = Field(default_factory=list)
    total: int = Field(default=0)
    success_count: int = Field(default=0)
    failed_count: int = Field(default=0)
    mock_count: int = Field(default=0)

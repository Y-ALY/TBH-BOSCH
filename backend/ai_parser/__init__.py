"""AI Parsing Backend — GDPR data classification layer.

This module sits between the regex scanner and the dashboard/database.
It takes scanner findings (raw suspicious snippets), passes them to an AI
classifier (mock or OpenRouter live), and returns structured GDPR metadata.

Quick start:
    from backend.ai_parser import AIParser, ScannerFinding

    parser = AIParser(mode="mock")
    result = parser.classify(ScannerFinding(
        file_id="local:test.pdf",
        file_name="test.pdf",
        snippet="Employee: Sara Hoffmann (E-20491)",
        regex_type="employee_id",
        regex_value="E-20491",
    ))
    print(result.dashboard_label)
"""

from .ai_parser import AIParser
from .openrouter_client import OpenRouterClient, OpenRouterError
from .schemas import (
    BatchClassificationResult,
    ClassificationResult,
    ScannerFinding,
)

__all__ = [
    "AIParser",
    "OpenRouterClient",
    "OpenRouterError",
    "ScannerFinding",
    "ClassificationResult",
    "BatchClassificationResult",
]

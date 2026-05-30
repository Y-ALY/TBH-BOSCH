from src.scanner import run_ai_scan
from src.models import ScanResult, ParsedDocument, Finding
from unittest.mock import MagicMock

def test_ai_gatekeeper():
    print("==================================================")
    print("Testing AI Gatekeeper Logic")
    print("==================================================\n")
    
    # 1. Mock the connector (bypass file downloads)
    mock_connector = MagicMock()
    mock_connector.list_files.return_value = []
    
    # 2. Setup mock documents
    # Doc 1: Confident and Clean -> SHOULD BE SKIPPED
    doc_confident_clean = ParsedDocument(
        file_id="doc1", file_name="invoice.pdf", source_type="local", 
        document_type="invoice", # CONFIDENT TYPE
        page_count=1, text_length=100, content_hash="a", owner_hints=[], 
        needs_ocr=False, pages=[], fields={}
    )
    
    # Doc 2: Unknown Type -> SHOULD BE SCANNED BY AI
    doc_unknown = ParsedDocument(
        file_id="doc2", file_name="messy_scan.pdf", source_type="local", 
        document_type="unknown", # UNKNOWN TYPE
        page_count=1, text_length=100, content_hash="b", owner_hints=[], 
        needs_ocr=False, pages=[], fields={}
    )
    
    # Doc 3: Confident but has Regex Findings -> SHOULD BE SCANNED BY AI
    doc_confident_but_findings = ParsedDocument(
        file_id="doc3", file_name="resume.pdf", source_type="local", 
        document_type="resume", # CONFIDENT TYPE
        page_count=1, text_length=100, content_hash="c", owner_hints=[], 
        needs_ocr=False, pages=[], fields={}
    )
    
    # Add one finding for doc3
    finding = Finding(finding_id="1", file_id="doc3", type="email", value="test@test.com", field="", context="", risk_level="low", confidence=1.0, evidence="", recommended_action="")
    
    mock_result = ScanResult(scan_id="1", timestamp="now", connector_type="mock", files_scanned=3, change_token="")
    mock_result.parsed_documents = [doc_confident_clean, doc_unknown, doc_confident_but_findings]
    mock_result.findings = [finding]

    # 3. Patch run_full_scan to return our mock result
    import src.scanner
    src.scanner.run_full_scan = MagicMock(return_value=mock_result)

    # 4. Mock the AI parser to track what it scans
    class MockAIParser:
        def __init__(self):
            self.scanned_docs = []
            
        def parse(self, text, fields, page_count, regex_findings_count):
            self.scanned_docs.append(regex_findings_count)
            # return a dummy AI result
            mock_ai_result = MagicMock()
            mock_ai_result.findings = []
            mock_ai_result.confidence = 0.9
            mock_ai_result.document_type = "ai_classified"
            return mock_ai_result

    mock_ai_parser = MockAIParser()

    # 5. Run the scan!
    run_ai_scan(mock_connector, ai_parser=mock_ai_parser)

    # 6. Print Results
    print(f"Total documents passed to pipeline: {len(mock_result.parsed_documents)}")
    print(f"Total documents sent to AI Parser:  {len(mock_ai_parser.scanned_docs)}\n")
    
    print("Detailed breakdown:")
    print("1. doc_confident_clean        (Type: invoice, Findings: 0) -> AI SCANNED?", doc_confident_clean.fields.get('_ai_model') is not None)
    print("2. doc_unknown                (Type: unknown, Findings: 0) -> AI SCANNED?", doc_unknown.fields.get('_ai_model') is not None)
    print("3. doc_confident_but_findings (Type: resume,  Findings: 1) -> AI SCANNED?", doc_confident_but_findings.fields.get('_ai_model') is not None)

if __name__ == "__main__":
    test_ai_gatekeeper()

import re
import sqlite3
import os
from datetime import datetime
from typing import List, Iterator, Optional
from pydantic import BaseModel, Field

# ==========================================
# 1. DATA MODELS
# ==========================================

class Document(BaseModel):
    """Input model representing a document to be scanned."""
    document_id: str
    file_name: str
    last_modified: datetime
    content: str

class FlaggedDocument(BaseModel):
    """Output model representing a discovered piece of PII."""
    document_id: str
    file_name: str
    pii_type: str = Field(description="e.g., EMAIL, PHONE, IBAN")
    value: str = Field(description="The actual extracted data")
    snippet: str = Field(description="Context window around the match for human review")

# ==========================================
# 2. SCANNER ENGINE
# ==========================================

class GDPRFastScanner:
    def __init__(self, db_path: str = "scan_state.db"):
        """Initialize the scanner and the Delta Scan state database."""
        self.db_path = db_path
        self._init_db()
        
        # Compile Regex patterns for speed
        self.patterns = {
            "EMAIL": re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'),
            "PHONE": re.compile(r'\+?\d{1,4}?[-.\s]?\(?\d{1,3}?\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}'),
            # Broad IBAN pattern for European formats
            "IBAN": re.compile(r'[A-Z]{2}\d{2}[A-Z0-9]{11,30}') 
        }

    def _init_db(self):
        """Creates a lightweight table to track document timestamps for Delta Scans."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scan_history (
                    document_id TEXT PRIMARY KEY,
                    last_modified TEXT
                )
            ''')
            conn.commit()

    def _requires_scan(self, doc: Document) -> bool:
        """Checks if a document is new or modified since the last scan."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT last_modified FROM scan_history WHERE document_id = ?', (doc.document_id,))
            result = cursor.fetchone()
            
            if not result:
                return True # New document
            
            last_scanned_time = datetime.fromisoformat(result[0])
            return doc.last_modified > last_scanned_time # Modified document

    def _update_scan_state(self, doc: Document):
        """Updates the database after a successful scan."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO scan_history (document_id, last_modified)
                VALUES (?, ?)
            ''', (doc.document_id, doc.last_modified.isoformat()))
            conn.commit()

    def _get_snippet(self, text: str, start: int, end: int, window: int = 40) -> str:
        """Extracts text around the match to provide context to the human reviewer."""
        snippet_start = max(0, start - window)
        snippet_end = min(len(text), end + window)
        snippet = text[snippet_start:snippet_end].replace('\n', ' ').strip()
        return f"...{snippet}..."

    def scan_document(self, doc: Document) -> List[FlaggedDocument]:
        """Scans a single document for all defined PII patterns."""
        flags = []
        for pii_type, pattern in self.patterns.items():
            for match in pattern.finditer(doc.content):
                value = match.group()
                
                # Basic cleanup for false positive phone numbers (e.g., dates/IDs)
                if pii_type == "PHONE" and len(re.sub(r'\D', '', value)) < 10:
                    continue
                    
                snippet = self._get_snippet(doc.content, match.start(), match.end())
                
                flags.append(FlaggedDocument(
                    document_id=doc.document_id,
                    file_name=doc.file_name,
                    pii_type=pii_type,
                    value=value,
                    snippet=snippet
                ))
        return flags

    def process_batch(self, documents: List[Document]) -> Iterator[FlaggedDocument]:
        """
        Generator that yields flagged documents one by one.
        Highly memory-efficient for massive datasets.
        """
        for doc in documents:
            if self._requires_scan(doc):
                flags = self.scan_document(doc)
                for flag in flags:
                    yield flag
                self._update_scan_state(doc)
            else:
                pass # Skip unchanged files (Delta Scan logic)

# ==========================================
# 3. HACKATHON TEST EXECUTION
# ==========================================

if __name__ == "__main__":
    # Mock data mimicking the challenge requirements
    mock_content = """
    Employee: Jonas Weber
    Reimbursement Account:
    IBAN: DE89370400440532013000
    
    External Consultant: Sarah Jenkins
    Contact Info: For verification, contact her directly at s.jenkins@cloud-arch.com or via her mobile network at +49 151 23456789.
    """
    
    mock_doc = Document(
        document_id="DOC-2026-89421",
        file_name="expense_report_v1.pdf",
        last_modified=datetime.now(),
        content=mock_content
    )

    # Initialize the scanner
    scanner = GDPRFastScanner(db_path="hackathon_delta_state.db")
    
    print("🚀 Starting GDPR Fast Scan...\n")
    
    # Run the batch processor
    results = list(scanner.process_batch([mock_doc]))
    
    if not results:
        print("✅ No new PII found or document unchanged.")
    else:
        print(f"⚠️ Found {len(results)} PII instances requiring human review:\n")
        for res in results:
            print(f"[{res.pii_type}] in {res.file_name}")
            print(f"Value: {res.value}")
            print(f"Context: {res.snippet}\n")
            print("-" * 50)
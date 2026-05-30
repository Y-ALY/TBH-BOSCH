#!/usr/bin/env python3
"""
test_pdf_pipeline.py – Tests the PyMuPDF PDF extraction and PII scanning pipeline
by generating a PDF directly using PyMuPDF (avoiding Pillow/ReportLab arch mismatches).
"""

import logging
from pathlib import Path
from pii_filter import FastFilterPipeline, ingest_directory
from pii_filter.state_manager import InMemoryStateManager

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s │ %(levelname)-7s │ %(message)s")
log = logging.getLogger("test_pdf")

def create_test_pdf(output_path: Path):
    """
    Creates a simple PDF document containing dummy PII using PyMuPDF.
    This avoids external library dependencies like reportlab/Pillow.
    """
    import fitz  # PyMuPDF

    # Create a new empty PDF doc
    doc = fitz.open()
    page = doc.new_page()  # default A4 size
    
    # Define text with some structured PII
    text = (
        "CONFIDENTIAL PDF DOCUMENT\n\n"
        "This document contains test PII to verify our GDPR discovery pipeline.\n\n"
        "Primary Consultant Contact:\n"
        "Email: sarah.connor@cyberdyne-systems.com\n"
        "Phone: +49 151 98765432\n"
        "Bank details:\n"
        "IBAN: DE89 3704 0044 0532 0130 00\n\n"
        "Please handle this document securely."
    )

    # Insert text into the page (top-left offset)
    page.insert_text((50, 72), text, fontsize=11)
    
    # Save document
    doc.save(str(output_path))
    doc.close()
    log.info("✅ Created test PDF: %s", output_path)

def main():
    test_dir = Path("./test_pdf_docs")
    test_dir.mkdir(exist_ok=True)
    pdf_path = test_dir / "confidential_file.pdf"

    # 1. Generate the PDF
    create_test_pdf(pdf_path)

    # 2. Run the ingestion scanner
    log.info("Starting pipeline scan over directory: %s", test_dir)
    pipeline = FastFilterPipeline(state_manager=InMemoryStateManager())
    
    flagged_docs = pipeline.process_batch(ingest_directory(test_dir))

    # 3. Print verification output
    if not flagged_docs:
        log.error("❌ Test failed: No PII was flagged in the PDF!")
    else:
        for doc in flagged_docs:
            log.info("🎉 Success! Scanned PDF document: %s", doc.file_name)
            log.info("Matches found (%d total):", doc.match_count)
            for m in doc.matches:
                log.info("  -> [%s] Value: %s", m.pii_type.value.upper(), m.matched_value)

if __name__ == "__main__":
    main()

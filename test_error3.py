import sys
import os

file_path = "./strict_drive/A2/update_pascale.häring34_A2D4D0.pdf"

try:
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    
    # Try parsing
    from src.pdf_parser import parse_pdf
    pages, needs_ocr = parse_pdf(file_bytes)
    print("Parsed pages:", len(pages))
    
    # Try regex
    from src.extractor import _extract_matches_from_text
    seen = set()
    for p in pages:
        findings = _extract_matches_from_text(p.text, seen)
        print("Findings in page:", len(findings))

except Exception as e:
    import traceback
    traceback.print_exc()


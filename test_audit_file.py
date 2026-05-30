from pii_filter.pii_scanner import PIIScanner

def test_file():
    # Read the file
    file_path = "data/sample_pdfs/airline_it_audit.txt"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    print(f"Scanning: {file_path}")
    print("-" * 50)
    
    # Initialize the scanner
    scanner = PIIScanner()
    
    # Run the scan
    matches = scanner.scan(content)
    
    if not matches:
        print("No PII found.")
        return
        
    print(f"Found {len(matches)} PII matches:\n")
    for match in matches:
        print(f"[{match.pii_type.value.upper()}] Offset: {match.char_offset}")
        print(f"Value:   {match.matched_value}")
        print(f"Snippet: {match.snippet}")
        print("-" * 40)

if __name__ == "__main__":
    test_file()

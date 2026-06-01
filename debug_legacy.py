import sys
from src.legacy_scanner import PIIScanner

scanner = PIIScanner()
try:
    scanner.scan("./strict_drive/A2/update_pascale.häring34_A2D4D0.pdf")
except Exception as e:
    import traceback
    traceback.print_exc()

import sys
from src.pdf_parser import parse_pdf
from src.extractor import read_file_chunks

# Let's try reading a file with read_file_chunks
file_path = "./strict_drive/A2/update_pascale.häring34_A2D4D0.pdf"
try:
    for chunk in read_file_chunks(file_path):
        pass
    print("Success reading!")
except Exception as e:
    import traceback
    traceback.print_exc()


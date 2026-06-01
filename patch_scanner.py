import re

with open('src/streaming_scanner.py', 'r') as f:
    content = f.read()

# Add _scan_memory_chunk
new_func = """
def _scan_memory_chunk(file_bytes: bytes) -> Tuple[list, dict, str, int, bool]:
    # Check if it looks like a PDF
    if file_bytes.startswith(b"%PDF"):
        import fitz
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            text_parts = []
            pages = []
            total_chars = 0
            for i in range(len(doc)):
                page = doc[i]
                page_text = page.get_text().strip()
                text_parts.append(page_text)
                char_count = len(page_text)
                total_chars += char_count
                pages.append(PageContent(page_number=i+1, text=page_text, char_count=char_count))
            doc.close()
            full_text = "\\n".join(text_parts)
            needs_ocr = total_chars == 0
        except Exception:
            full_text = file_bytes.decode('utf-8', errors='replace')
            needs_ocr = False
            pages = [PageContent(page_number=1, text=full_text, char_count=len(full_text))]
    else:
        full_text = file_bytes.decode('utf-8', errors='replace')
        needs_ocr = False
        pages = [PageContent(page_number=1, text=full_text, char_count=len(full_text))]
        
    findings = []
    fields = {}
    doc_type = "unknown"
    if not needs_ocr:
        findings = extract_entities(full_text, pages)
        fields = _extract_fields(full_text)
        doc_type = classify_context(full_text, fields)
        
    return findings, fields, doc_type, len(full_text), needs_ocr
"""

# Insert _scan_memory_chunk after _scan_text_chunk
content = content.replace("def _scan_text_chunk", new_func + "\ndef _scan_text_chunk")

# Replace the skip logic
old_skip = """            if not file_path or not os.path.exists(file_path):
                _emit_error(on_error, file_ref, "io_error", "Missing or non-local file path for mmap.")
                metrics.files_error += 1
                continue
                
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                _emit_error(on_error, file_ref, "parse_error", "Empty file (0 bytes)")
                metrics.files_error += 1
                continue

            parse_start = time.monotonic()
            chunks_to_submit = []
            num_pages = 1
            
            try:
                if file_path.lower().endswith(".pdf"):
                    import fitz
                    with fitz.open(file_path) as doc:
                        num_pages = len(doc)
                    
                    chunk_size = 10
                    for start_page in range(0, num_pages, chunk_size):
                        end_page = start_page + chunk_size
                        chunks_to_submit.append((_scan_pdf_chunk, (file_path, start_page, end_page)))
                else:
                    chunk_size = 1024 * 1024
                    for start_byte in range(0, file_size, chunk_size):
                        end_byte = start_byte + chunk_size
                        chunks_to_submit.append((_scan_text_chunk, (file_path, start_byte, end_byte)))
            except Exception as exc:
                _emit_error(on_error, file_ref, "parse_error", str(exc))
                metrics.files_error += 1
                continue"""

new_skip = """            parse_start = time.monotonic()
            chunks_to_submit = []
            num_pages = 1

            if not file_path or not os.path.exists(file_path):
                try:
                    file_bytes = connector.download_file(file_ref.file_id)
                    chunks_to_submit = [(_scan_memory_chunk, (file_bytes,))]
                except Exception as exc:
                    _emit_error(on_error, file_ref, "parse_error", str(exc))
                    metrics.files_error += 1
                    continue
            else:
                file_size = os.path.getsize(file_path)
                if file_size == 0:
                    _emit_error(on_error, file_ref, "parse_error", "Empty file (0 bytes)")
                    metrics.files_error += 1
                    continue
                
                try:
                    if file_path.lower().endswith(".pdf"):
                        import fitz
                        with fitz.open(file_path) as doc:
                            num_pages = len(doc)
                        
                        chunk_size = 10
                        for start_page in range(0, num_pages, chunk_size):
                            end_page = start_page + chunk_size
                            chunks_to_submit.append((_scan_pdf_chunk, (file_path, start_page, end_page)))
                    else:
                        chunk_size = 1024 * 1024
                        for start_byte in range(0, file_size, chunk_size):
                            end_byte = start_byte + chunk_size
                            chunks_to_submit.append((_scan_text_chunk, (file_path, start_byte, end_byte)))
                except Exception as exc:
                    _emit_error(on_error, file_ref, "parse_error", str(exc))
                    metrics.files_error += 1
                    continue"""

content = content.replace(old_skip, new_skip)

with open('src/streaming_scanner.py', 'w') as f:
    f.write(content)

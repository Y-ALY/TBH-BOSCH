with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

broken_part = """    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"OCR processing failed: {str(exc)}",
        )
    finally:
        # Drop the raw bytes reference to free memory immediately
    import os
    
    if not session_emp_id:"""

fixed_part = """    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"OCR processing failed: {str(exc)}",
        )
    finally:
        # Drop the raw bytes reference to free memory immediately
        del file_bytes

    return result


@app.get("/api/scan/image/cache-stats")
def ocr_cache_stats():
    '''Return basic OCR cache statistics (admin/debug endpoint).'''
    from src.ocr_scanner import get_cache_stats
    return get_cache_stats()


@app.post("/api/scan/image/clear-cache")
def ocr_clear_cache():
    '''Flush the OCR result cache (admin endpoint).'''
    from src.ocr_scanner import clear_cache
    evicted = clear_cache()
    return {"status": "success", "evicted_entries": evicted}


from fastapi.responses import FileResponse
from fastapi import Cookie
from typing import Optional

@app.get("/api/files/{file_id}/view")
def view_file_content(
    file_id: int,
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    from fastapi import HTTPException
    import os
    
    if not session_emp_id:"""

if broken_part in content:
    content = content.replace(broken_part, fixed_part)
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed successfully!')
else:
    print('Broken part not found.')

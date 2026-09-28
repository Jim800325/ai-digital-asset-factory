from pathlib import Path
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIR=Path(__file__).resolve().parent/"static"
REVIEW_HTML=STATIC_DIR/"review.html"

router=APIRouter(include_in_schema=False)

_SECURITY_HEADERS={
    "Cache-Control":"no-store",
    "X-Content-Type-Options":"nosniff",
    "Referrer-Policy":"no-referrer",
    "Content-Security-Policy":(
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    ),
}

def _review_file():
    return FileResponse(
        REVIEW_HTML,
        media_type="text/html; charset=utf-8",
        headers=_SECURITY_HEADERS,
    )

@router.get("/review")
def review_workspace_page():
    return _review_file()

@router.get("/review/{candidate_id}")
def review_workspace_candidate_page(candidate_id:UUID):
    return _review_file()

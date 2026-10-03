from pathlib import Path
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIR=Path(__file__).resolve().parent/"static"
REVIEW_HTML=STATIC_DIR/"review.html"
AUDITS_HTML=STATIC_DIR/"audits.html"
SHRIMP_REVIEW_HTML=STATIC_DIR/"shrimp-review.html"

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

def _html_file(path:Path):
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers=_SECURITY_HEADERS,
    )


def _review_file():
    return _html_file(REVIEW_HTML)

@router.get("/review")
def review_workspace_page():
    return _review_file()

@router.get("/review/audits")
def live_acceptance_audit_registry_page():
    return _html_file(AUDITS_HTML)

@router.get("/review/audits/{audit_id}")
def live_acceptance_audit_registry_detail_page(audit_id:str):
    return _html_file(AUDITS_HTML)

@router.get("/review/{candidate_id}")
def review_workspace_candidate_page(candidate_id:UUID):
    return _review_file()


@router.get("/animation-review")
def shrimp_review_workspace_page():
    return _html_file(SHRIMP_REVIEW_HTML)

@router.get("/animation-review/{job_id}")
def shrimp_review_workspace_job_page(job_id:UUID):
    return _html_file(SHRIMP_REVIEW_HTML)

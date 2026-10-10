from pathlib import Path
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIR=Path(__file__).resolve().parent/"static"
REVIEW_HTML=STATIC_DIR/"review.html"
AUDITS_HTML=STATIC_DIR/"audits.html"
CONTROL_CENTER_HOME_HTML=STATIC_DIR/"production-home.html"
MANUAL_PIPELINE_HTML=STATIC_DIR/"manual-pipeline.html"

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

@router.get("/")
def unified_control_center_homepage():
    return _html_file(CONTROL_CENTER_HOME_HTML)

@router.get("/manual-pipeline")
def manual_pipeline_workspace_page():
    return _html_file(MANUAL_PIPELINE_HTML)

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

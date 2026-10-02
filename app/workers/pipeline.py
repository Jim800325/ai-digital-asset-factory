import hashlib
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text

from app.build_proposals import refresh_build_proposals
from app.config import settings
from app.db import engine
from app.ingest import ingest_discovery_item
from app.providers.github import discover_github
from app.provider_composition import (
    composition_discovery_items,
    run_provider_composition_cycle,
)
from app.research import refresh_candidate_reports
from app.side_business_registry import (
    build_ready_provider_discovery_items,
    run_side_business_registry_cycle,
)
from app.validation import refresh_candidate_validations
from app.url_safety import is_public_http_url, safe_url_syntax

def _clean(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script","style","noscript","svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return title[:500], body[:100000], soup

def _discover_links(base: str, soup: BeautifulSoup):
    out=[]
    for a in soup.find_all("a", href=True):
        u=urljoin(base,a["href"])
        p=urlparse(u)
        if p.scheme in ("http","https") and p.netloc and safe_url_syntax(u):
            out.append(u.split("#")[0])
    return list(dict.fromkeys(out))

def _safe_get(client: httpx.Client, url: str, max_redirects: int = 5):
    current=url
    for _ in range(max_redirects+1):
        if not is_public_http_url(current):
            return None
        response=client.get(current)
        if response.status_code in (301,302,303,307,308):
            location=response.headers.get("location")
            if not location:
                return response
            current=urljoin(str(response.url),location)
            continue
        return response
    return None

def _web_item(url: str, title: str, content: str) -> dict:
    fingerprint=hashlib.sha256(("WEB_PAGE|"+url).encode("utf-8")).hexdigest()
    return {
        "source_type":"WEB_PAGE",
        "url":url,
        "title":title or url,
        "text":content,
        "fingerprint":fingerprint,
    }

def _apply_ingest_result(result, counters: dict) -> None:
    if not result:
        return
    counters["evidence"]+=int(result["evidence_created"])
    counters["opportunities"]+=int(result["opportunity_created"])
    counters["reports"]+=int(result["research_report_generated"])
    counters["validations"]+=int(result["research_validation_generated"])

def run_pipeline(acceptance_items: list[dict] | None = None):
    run_id=None
    try:
        with engine.begin() as db:
            run_id=db.execute(text("INSERT INTO pipeline_runs DEFAULT VALUES RETURNING id")).scalar_one()

        acceptance_mode=acceptance_items is not None
        urls=[] if acceptance_mode else list(settings.seeds)
        github_items=[]
        registry_items=[]
        registry_result={"status":"SKIPPED"}
        composition_items=[]
        composition_result={"status":"SKIPPED"}

        if not acceptance_mode and settings.side_business_registry_enabled:
            try:
                registry_result=run_side_business_registry_cycle()
                registry_items=build_ready_provider_discovery_items(limit=20)
                print(
                    "side-business registry: "
                    f"{registry_result.get('status')} "
                    f"refreshed={registry_result.get('refreshed',0)} "
                    f"build_ready={registry_result.get('build_ready',0)}",
                    flush=True,
                )
            except Exception as exc:
                registry_result={"status":"FAILED","error":str(exc)[:1000]}
                print(f"side-business registry skipped: {exc}", flush=True)

        if not acceptance_mode and settings.side_business_composition_enabled:
            try:
                composition_result=run_provider_composition_cycle()
                composition_items=composition_discovery_items()
                print(
                    "provider composition planner: "
                    f"{composition_result.get('status')} "
                    f"generated={composition_result.get('compositions_generated',0)} "
                    f"active={composition_result.get('active_compositions',0)} "
                    f"emitted={len(composition_items)}",
                    flush=True,
                )
            except Exception as exc:
                composition_result={"status":"FAILED","error":str(exc)[:1000]}
                print(f"provider composition planner skipped: {exc}", flush=True)

        if not acceptance_mode and settings.github_discovery_enabled:
            try:
                github_items=discover_github(settings.github_results_per_query)
            except Exception as exc:
                print(f"github discovery skipped: {exc}", flush=True)

        crawled=0
        counters={"evidence":0,"opportunities":0,"reports":0,"validations":0}
        seen=set()

        if not acceptance_mode:
            with httpx.Client(
                headers={"User-Agent":settings.user_agent},
                timeout=settings.request_timeout_seconds,
                follow_redirects=False,
            ) as client:
                i=0
                while i < len(urls) and crawled < settings.max_pages_per_run:
                    url=urls[i]
                    i+=1
                    if url in seen:
                        continue
                    seen.add(url)
                    try:
                        r=_safe_get(client,url)
                        if r is None or r.status_code != 200 or "text/html" not in r.headers.get("content-type",""):
                            continue
                        title,content,soup=_clean(r.text)
                        if len(content)<200:
                            continue
                        crawled+=1
                        final_url=str(r.url)
                        if crawled <= 5:
                            urls.extend(_discover_links(final_url,soup)[:10])
                        _apply_ingest_result(
                            ingest_discovery_item(_web_item(final_url,title,content)),
                            counters,
                        )
                    except Exception as exc:
                        print(f"web ingest skipped: {url}: {exc}", flush=True)

        provider_items=(
            acceptance_items
            if acceptance_mode
            else github_items + registry_items + composition_items
        )
        for item in provider_items:
            try:
                _apply_ingest_result(ingest_discovery_item(item),counters)
            except Exception as exc:
                label="acceptance" if acceptance_mode else "github"
                print(f"{label} ingest skipped: {exc}", flush=True)

        reports_generated=0
        try:
            reports_generated=refresh_candidate_reports()
            print(f"research reports refreshed: {reports_generated}", flush=True)
        except Exception as exc:
            print(f"research report refresh skipped: {exc}", flush=True)

        validations_generated=0
        try:
            validations_generated=refresh_candidate_validations()
            print(f"research validations refreshed: {validations_generated}", flush=True)
        except Exception as exc:
            print(f"research validation refresh skipped: {exc}", flush=True)

        proposals_generated=0
        try:
            proposals_generated=refresh_build_proposals()
            print(f"build proposals refreshed: {proposals_generated}", flush=True)
        except Exception as exc:
            print(f"build proposal refresh skipped: {exc}", flush=True)

        counters["reports"]+=reports_generated
        counters["validations"]+=validations_generated
        discovered=len(acceptance_items) if acceptance_mode else len(set(urls))

        with engine.begin() as db:
            db.execute(text("""
              UPDATE pipeline_runs
              SET status='SUCCESS',
                  pages_discovered=:pd,
                  pages_crawled=:pc,
                  evidence_created=:ec,
                  opportunities_created=:oc,
                  finished_at=now()
              WHERE id=:id
            """),{
                "pd":discovered,
                "pc":crawled,
                "ec":counters["evidence"],
                "oc":counters["opportunities"],
                "id":run_id,
            })

        return {
            "run_id":str(run_id),
            "mode":"ACCEPTANCE" if acceptance_mode else "OBSERVE",
            "crawled":crawled,
            "evidence":counters["evidence"],
            "opportunities":counters["opportunities"],
            "research_reports":counters["reports"],
            "research_validations":counters["validations"],
            "build_proposals":proposals_generated,
            "side_business_registry":registry_result,
            "provider_composition_planner":composition_result,
        }
    except Exception as exc:
        if run_id:
            with engine.begin() as db:
                db.execute(
                    text("""
                      UPDATE pipeline_runs
                      SET status='FAILED',error=:e,finished_at=now()
                      WHERE id=:id
                    """),
                    {"e":str(exc)[:4000],"id":run_id},
                )
        raise

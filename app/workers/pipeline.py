import hashlib
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.ingest import ingest_discovery_item
from app.providers.github import discover_github
from app.research import refresh_candidate_reports

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
        if p.scheme in ("http","https") and p.netloc:
            out.append(u.split("#")[0])
    return list(dict.fromkeys(out))

def _web_item(url: str, title: str, content: str) -> dict:
    fingerprint=hashlib.sha256(("WEB_PAGE|"+url).encode("utf-8")).hexdigest()
    return {
        "source_type":"WEB_PAGE",
        "url":url,
        "title":title or url,
        "text":content,
        "fingerprint":fingerprint,
    }

def run_pipeline():
    run_id=None
    try:
        with engine.begin() as db:
            run_id=db.execute(text("INSERT INTO pipeline_runs DEFAULT VALUES RETURNING id")).scalar_one()

        urls=list(settings.seeds)
        github_items=[]
        if settings.github_discovery_enabled:
            try:
                github_items=discover_github(settings.github_results_per_query)
            except Exception as exc:
                print(f"github discovery skipped: {exc}", flush=True)

        crawled=ev_count=opp_count=0
        seen=set()

        with httpx.Client(headers={"User-Agent":settings.user_agent}, timeout=settings.request_timeout_seconds, follow_redirects=True) as client:
            i=0
            while i < len(urls) and crawled < settings.max_pages_per_run:
                url=urls[i]; i+=1
                if url in seen:
                    continue
                seen.add(url)
                try:
                    r=client.get(url)
                    if r.status_code != 200 or "text/html" not in r.headers.get("content-type",""):
                        continue
                    title,content,soup=_clean(r.text)
                    if len(content)<200:
                        continue
                    crawled+=1
                    final_url=str(r.url)
                    if crawled <= 5:
                        urls.extend(_discover_links(final_url,soup)[:10])
                    if ingest_discovery_item(_web_item(final_url,title,content)):
                        ev_count+=1
                        opp_count+=1
                except Exception as exc:
                    print(f"web ingest skipped: {url}: {exc}", flush=True)

        for item in github_items:
            try:
                if ingest_discovery_item(item):
                    ev_count+=1
                    opp_count+=1
            except Exception as exc:
                print(f"github ingest skipped: {exc}", flush=True)

        try:
            reports_generated=refresh_candidate_reports()
            print(f"research reports refreshed: {reports_generated}", flush=True)
        except Exception as exc:
            print(f"research report refresh skipped: {exc}", flush=True)

        with engine.begin() as db:
            db.execute(text("""
              UPDATE pipeline_runs SET status='SUCCESS',pages_discovered=:pd,pages_crawled=:pc,
                evidence_created=:ec,opportunities_created=:oc,finished_at=now() WHERE id=:id
            """),{"pd":len(set(urls)),"pc":crawled,"ec":ev_count,"oc":opp_count,"id":run_id})
        return {"run_id":str(run_id),"crawled":crawled,"evidence":ev_count,"opportunities":opp_count}
    except Exception as exc:
        if run_id:
            with engine.begin() as db:
                db.execute(text("UPDATE pipeline_runs SET status='FAILED',error=:e,finished_at=now() WHERE id=:id"),
                           {"e":str(exc)[:4000],"id":run_id})
        raise

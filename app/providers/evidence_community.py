from __future__ import annotations

import html
import re

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.evidence_quality import normalized_domain


HN_API = "https://hn.algolia.com/api/v1/search"
STACK_API = "https://api.stackexchange.com/2.3/search/advanced"


def _plain(value: str | None, limit: int = 12000) -> str:
    if not value:
        return ""
    text=BeautifulSoup(html.unescape(value),"html.parser").get_text(" ",strip=True)
    return re.sub(r"\s+"," ",text).strip()[:limit]


def search_hacker_news(query: str, limit: int) -> list[dict]:
    safe_limit=min(max(int(limit),1),10)
    with httpx.Client(
        timeout=settings.request_timeout_seconds,
        headers={"User-Agent":settings.user_agent},
        follow_redirects=False,
    ) as client:
        response=client.get(
            HN_API,
            params={
                "query":query[:180],
                "tags":"story",
                "hitsPerPage":safe_limit,
            },
        )
        response.raise_for_status()
        payload=response.json()

    out=[]
    for item in payload.get("hits",[])[:safe_limit]:
        object_id=str(item.get("objectID") or "").strip()
        title=_plain(item.get("title") or item.get("story_title"),500)
        if not object_id or not title:
            continue
        source_url=f"https://news.ycombinator.com/item?id={object_id}"
        text=" ".join(filter(None,[
            title,
            _plain(item.get("story_text"),8000),
            str(item.get("url") or "").strip(),
        ]))
        out.append({
            "provider":"HACKER_NEWS_ALGOLIA",
            "source_type":"COMMUNITY_HN",
            "source_class":"community",
            "url":source_url,
            "title":title,
            "text":text[:12000],
        })
    return out


STACK_SITES = {
    "stackoverflow":"stackoverflow.com",
    "softwareengineering":"softwareengineering.stackexchange.com",
    "superuser":"superuser.com",
}

def search_stackexchange(query: str, limit: int, site: str = "stackoverflow") -> list[dict]:
    safe_limit=min(max(int(limit),1),10)
    expected_domain=STACK_SITES.get(site)
    if not expected_domain:
        raise ValueError("Unsupported Stack Exchange site")
    with httpx.Client(
        timeout=settings.request_timeout_seconds,
        headers={"User-Agent":settings.user_agent},
        follow_redirects=False,
    ) as client:
        response=client.get(
            STACK_API,
            params={
                "site":site,
                "q":query[:180],
                "order":"desc",
                "sort":"relevance",
                "pagesize":safe_limit,
                "filter":"withbody",
            },
        )
        response.raise_for_status()
        payload=response.json()

    out=[]
    for item in payload.get("items",[])[:safe_limit]:
        link=str(item.get("link") or "").strip()
        if normalized_domain(link)!=expected_domain:
            continue
        title=_plain(item.get("title"),500)
        if not title:
            continue
        tags=" ".join(str(tag) for tag in item.get("tags") or [])
        text=" ".join(filter(None,[title,tags,_plain(item.get("body"),10000)]))
        out.append({
            "provider":f"STACKEXCHANGE_API:{site}",
            "source_type":f"COMMUNITY_STACKEXCHANGE_{site.upper()}",
            "source_class":"community",
            "url":link,
            "title":title,
            "text":text[:12000],
        })
    return out


def search_stackoverflow(query: str, limit: int) -> list[dict]:
    return search_stackexchange(query,limit,"stackoverflow")

def search_community(query: str, limit: int) -> tuple[list[dict],list[dict]]:
    results=[]
    errors=[]
    providers=[
        ("HACKER_NEWS_ALGOLIA",lambda: search_hacker_news(query,limit)),
    ]
    providers.extend(
        (f"STACKEXCHANGE_API:{site}",lambda site=site: search_stackexchange(query,limit,site))
        for site in STACK_SITES
    )
    for name,fn in providers:
        try:
            results.extend(fn())
        except Exception as exc:
            errors.append({"provider":name,"error":type(exc).__name__})
    unique={}
    for item in results:
        unique.setdefault(item["url"],item)
    return list(unique.values()),errors

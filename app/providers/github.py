import hashlib
import httpx
from app.config import settings

REPO_QUERIES = [
    "AI automation tool",
    "dataset API",
    "competitive intelligence",
    "workflow automation",
    "developer tool",
]
ISSUE_QUERIES = [
    '"looking for" tool',
    '"feature request" automation',
    '"too expensive" alternative',
    '"manual" workflow',
]

def _headers():
    h={"Accept":"application/vnd.github+json","User-Agent":settings.user_agent}
    if settings.github_token:
        h["Authorization"]=f"Bearer {settings.github_token}"
    return h

def discover_github(limit_per_query: int = 10) -> list[dict]:
    items=[]
    timeout=settings.request_timeout_seconds
    with httpx.Client(base_url="https://api.github.com",headers=_headers(),timeout=timeout) as c:
        for q in REPO_QUERIES:
            r=c.get("/search/repositories",params={"q":q,"sort":"updated","order":"desc","per_page":limit_per_query})
            if r.status_code != 200: continue
            for x in r.json().get("items",[]):
                text=" ".join(filter(None,[x.get("name"),x.get("description")," ".join(x.get("topics") or [])]))
                items.append({
                    "source_type":"GITHUB_REPOSITORY","url":x["html_url"],"title":x["full_name"],
                    "text":text,"external_id":str(x["id"]),
                    "fingerprint":hashlib.sha256(("repo:"+str(x["id"])).encode()).hexdigest()
                })
        for q in ISSUE_QUERIES:
            r=c.get("/search/issues",params={"q":q+" is:issue","sort":"updated","order":"desc","per_page":limit_per_query})
            if r.status_code != 200: continue
            for x in r.json().get("items",[]):
                text=" ".join(filter(None,[x.get("title"),x.get("body") or ""]))
                items.append({
                    "source_type":"GITHUB_ISSUE","url":x["html_url"],"title":x["title"],
                    "text":text[:30000],"external_id":str(x["id"]),
                    "fingerprint":hashlib.sha256(("issue:"+str(x["id"])).encode()).hexdigest()
                })
    unique={x["fingerprint"]:x for x in items}
    return list(unique.values())

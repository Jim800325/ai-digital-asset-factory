import hashlib
import re
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text
from app.config import settings
from app.db import engine

ASSET_RULES = [
    ("DATASET_API", ["dataset","data","api","database","directory","tracker","prices","pricing"]),
    ("INTELLIGENCE_REPORT", ["report","research","benchmark","market","trend","intelligence","analysis"]),
    ("MICRO_SAAS_TOOL", ["tool","generator","converter","automation","dashboard","monitor","software"]),
    ("TEMPLATE_WORKFLOW", ["template","workflow","playbook","prompt","checklist"]),
    ("CONTENT_IP", ["newsletter","guide","course","tutorial","content","video"]),
]
PAIN = ["manual","expensive","alternative","problem","need","wish","slow","difficult","hours","missing"]

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

def _classify(title: str, content: str):
    hay=(title+" "+content[:12000]).lower()
    ranked=[]
    for kind, words in ASSET_RULES:
        hits=sum(1 for w in words if w in hay)
        ranked.append((hits,kind))
    hits,kind=max(ranked)
    pain=sum(1 for w in PAIN if w in hay)
    return kind,hits,pain

def run_pipeline():
    run_id=None
    try:
        with engine.begin() as db:
            run_id=db.execute(text("INSERT INTO pipeline_runs DEFAULT VALUES RETURNING id")).scalar_one()

        urls=list(settings.seeds)
        crawled=ev_count=opp_count=0
        seen=set()

        with httpx.Client(headers={"User-Agent":settings.user_agent}, timeout=settings.request_timeout_seconds, follow_redirects=True) as client:
            i=0
            while i < len(urls) and crawled < settings.max_pages_per_run:
                url=urls[i]; i+=1
                if url in seen: continue
                seen.add(url)
                try:
                    r=client.get(url)
                    if r.status_code != 200 or "text/html" not in r.headers.get("content-type",""):
                        continue
                    title,content,soup=_clean(r.text)
                    if len(content)<200: continue
                    crawled+=1
                    if crawled <= 5:
                        urls.extend(_discover_links(str(r.url),soup)[:10])

                    digest=hashlib.sha256(content.encode()).hexdigest()
                    with engine.begin() as db:
                        doc_id=db.execute(text("""
                          INSERT INTO documents(url,title,content,content_hash)
                          VALUES(:u,:t,:c,:h)
                          ON CONFLICT(url) DO UPDATE SET title=excluded.title,content=excluded.content,
                            content_hash=excluded.content_hash,fetched_at=now()
                          RETURNING id
                        """),{"u":str(r.url),"t":title,"c":content,"h":digest}).scalar_one()

                        kind,hits,pain=_classify(title,content)
                        if hits:
                            excerpt=content[:1200]
                            db.execute(text("""
                              INSERT INTO evidence(document_id,signal_type,excerpt,source_url,confidence)
                              VALUES(:d,:s,:e,:u,:cf)
                            """),{"d":doc_id,"s":kind,"e":excerpt,"u":str(r.url),"cf":min(.95,.45+hits*.08+pain*.03)})
                            ev_count+=1

                            demand=min(100,35+pain*10+hits*4)
                            repeat=min(100,55+hits*6)
                            automation=min(100,50+hits*7)
                            ownership=85 if kind in ("DATASET_API","MICRO_SAAS_TOOL") else 72
                            margin=90 if kind in ("DATASET_API","TEMPLATE_WORKFLOW","CONTENT_IP") else 75
                            evidence=min(100,35+hits*10+pain*5)
                            score=round(demand*.20+repeat*.20+automation*.20+ownership*.15+margin*.15+evidence*.10,2)
                            fp=hashlib.sha256((kind+"|"+title.lower()[:180]).encode()).hexdigest()
                            status="CANDIDATE" if score>=75 else ("RESEARCH" if score>=60 else "WATCH")
                            result=db.execute(text("""
                              INSERT INTO digital_asset_opportunities(
                                fingerprint,title,asset_type,problem,target_customer,monetization_model,
                                source_url,demand_score,repeatability_score,automation_score,ownership_score,
                                marginal_cost_score,evidence_score,score,status)
                              VALUES(:fp,:title,:kind,:problem,:customer,:money,:url,:d,:r,:a,:o,:m,:e,:score,:status)
                              ON CONFLICT(fingerprint) DO UPDATE SET score=excluded.score,updated_at=now()
                              RETURNING (xmax = 0) AS inserted
                            """),{
                              "fp":fp,"title":title or str(r.url),"kind":kind,
                              "problem":"Detected recurring digital-asset signal; requires evidence validation.",
                              "customer":"To be validated in research stage",
                              "money":"subscription / one-time sale / API / licensing",
                              "url":str(r.url),"d":demand,"r":repeat,"a":automation,"o":ownership,
                              "m":margin,"e":evidence,"score":score,"status":status
                            }).scalar_one()
                            if result: opp_count+=1
                except Exception:
                    continue

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

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from app.config import settings
from app.db import engine


SEED_PROVIDERS = [
    {
        "repo_full_name": "n8n-io/n8n",
        "provider_role": "AUTOMATION",
        "license_policy": "CONDITIONAL",
        "license_notes": "Sustainable Use License; internal business automation is the preferred use. Review licensing before resale or hosted redistribution.",
        "commercial_model": "workflow automation / hosted cloud / enterprise",
        "commercial_fit": "INTERNAL_USE_RECOMMENDED",
        "automation_fit": 100.0,
    },
    {
        "repo_full_name": "activepieces/activepieces",
        "provider_role": "AUTOMATION",
        "license_policy": "PERMISSIVE",
        "license_notes": "Core repository is MIT with enterprise-only directories under separate terms.",
        "commercial_model": "workflow automation / embedded automation / enterprise",
        "commercial_fit": "COMMERCIAL_CORE_ALLOWED",
        "automation_fit": 100.0,
    },
    {
        "repo_full_name": "langgenius/dify",
        "provider_role": "AI_APP_PLATFORM",
        "license_policy": "CONDITIONAL",
        "license_notes": "Modified Apache terms include multi-tenant and frontend branding restrictions.",
        "commercial_model": "AI application platform / hosted cloud / enterprise",
        "commercial_fit": "INTERNAL_OR_SINGLE_TENANT",
        "automation_fit": 95.0,
    },
    {
        "repo_full_name": "langflow-ai/langflow",
        "provider_role": "AGENT_WORKFLOW",
        "license_policy": "PERMISSIVE",
        "license_notes": "MIT licensed core.",
        "commercial_model": "AI agent workflow platform / hosted service",
        "commercial_fit": "COMMERCIAL_ALLOWED",
        "automation_fit": 95.0,
    },
    {
        "repo_full_name": "gitroomhq/postiz-app",
        "provider_role": "DISTRIBUTION",
        "license_policy": "COPYLEFT",
        "license_notes": "AGPL-3.0; commercial use is possible subject to AGPL obligations.",
        "commercial_model": "social media scheduling / hosted SaaS",
        "commercial_fit": "AGPL_COMPLIANCE_REQUIRED",
        "automation_fit": 90.0,
    },
    {
        "repo_full_name": "knadh/listmonk",
        "provider_role": "NEWSLETTER",
        "license_policy": "COPYLEFT",
        "license_notes": "AGPL-3.0; commercial use is possible subject to AGPL obligations.",
        "commercial_model": "newsletter / mailing list management",
        "commercial_fit": "AGPL_COMPLIANCE_REQUIRED",
        "automation_fit": 88.0,
    },
    {
        "repo_full_name": "unclecode/crawl4ai",
        "provider_role": "DATA_COLLECTION",
        "license_policy": "PERMISSIVE",
        "license_notes": "Apache-2.0.",
        "commercial_model": "web data extraction / hosted cloud / API",
        "commercial_fit": "COMMERCIAL_ALLOWED",
        "automation_fit": 100.0,
    },
    {
        "repo_full_name": "apify/crawlee",
        "provider_role": "DATA_COLLECTION",
        "license_policy": "PERMISSIVE",
        "license_notes": "Apache-2.0.",
        "commercial_model": "web crawling / browser automation library",
        "commercial_fit": "COMMERCIAL_ALLOWED",
        "automation_fit": 98.0,
    },
    {
        "repo_full_name": "dgtlmoon/changedetection.io",
        "provider_role": "MONITORING",
        "license_policy": "PERMISSIVE",
        "license_notes": "Apache-2.0.",
        "commercial_model": "website monitoring / hosted SaaS",
        "commercial_fit": "COMMERCIAL_ALLOWED",
        "automation_fit": 96.0,
    },
    {
        "repo_full_name": "browser-use/browser-use",
        "provider_role": "BROWSER_AUTOMATION",
        "license_policy": "PERMISSIVE",
        "license_notes": "MIT.",
        "commercial_model": "browser agent automation / hosted service",
        "commercial_fit": "COMMERCIAL_ALLOWED",
        "automation_fit": 100.0,
    },
    {
        "repo_full_name": "DIYgod/RSSHub",
        "provider_role": "DATA_COLLECTION",
        "license_policy": "COPYLEFT",
        "license_notes": "AGPL-3.0; commercial use is possible subject to AGPL obligations.",
        "commercial_model": "RSS aggregation / information feeds",
        "commercial_fit": "AGPL_COMPLIANCE_REQUIRED",
        "automation_fit": 94.0,
    },
    {
        "repo_full_name": "bytechefhq/bytechef",
        "provider_role": "AUTOMATION",
        "license_policy": "PERMISSIVE",
        "license_notes": "Core is Apache-2.0 with enterprise-only directories under separate terms.",
        "commercial_model": "workflow automation / embedded iPaaS / enterprise",
        "commercial_fit": "COMMERCIAL_CORE_ALLOWED",
        "automation_fit": 100.0,
    },
]

SEED_BY_REPO = {item["repo_full_name"].lower(): item for item in SEED_PROVIDERS}

DISCOVERY_QUERY_TEMPLATES = [
    "self-hosted automation in:name,description stars:>100",
    "AI agent workflow in:name,description stars:>100",
    "web crawler scraper AI in:name,description stars:>100",
    "browser automation agent in:name,description stars:>100",
    "social media scheduler self-hosted in:name,description stars:>50",
    "newsletter self-hosted in:name,description stars:>50",
    "website monitoring self-hosted in:name,description stars:>50",
]

PERMISSIVE_LICENSES = {
    "MIT", "APACHE-2.0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "ISC", "ZLIB", "UNLICENSE",
}
WEAK_COPYLEFT_LICENSES = {"MPL-2.0", "LGPL-2.1", "LGPL-3.0", "EPL-1.0", "EPL-2.0"}
COPYLEFT_LICENSES = {"AGPL-3.0", "GPL-2.0", "GPL-3.0"}
AUTOMATION_TERMS = {
    "automation", "workflow", "agent", "agents", "crawler", "scraper", "monitor",
    "scheduler", "api", "mcp", "self-hosted", "self hosted", "newsletter",
    "browser", "social", "integration", "integrations", "rss", "pipeline",
}
MONETIZATION_TERMS = {
    "cloud", "hosted", "saas", "enterprise", "api", "subscription", "pricing",
    "commercial", "embedded", "platform", "service",
}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def license_policy_for(spdx: str | None) -> str:
    value = (spdx or "").strip().upper()
    if not value or value == "NOASSERTION":
        return "UNKNOWN"
    if value in PERMISSIVE_LICENSES:
        return "PERMISSIVE"
    if value in WEAK_COPYLEFT_LICENSES:
        return "WEAK_COPYLEFT"
    if value in COPYLEFT_LICENSES:
        return "COPYLEFT"
    if value.startswith("BUSL") or value.startswith("BSL-"):
        return "CONDITIONAL"
    return "UNKNOWN"


def _activity_score(pushed_at: datetime | None, now: datetime) -> float:
    if pushed_at is None:
        return 10.0
    days = max(0, (now - pushed_at).days)
    if days <= 7:
        return 100.0
    if days <= 30:
        return 92.0
    if days <= 90:
        return 78.0
    if days <= 180:
        return 60.0
    if days <= 365:
        return 38.0
    return 15.0


def _popularity_score(stars: int) -> float:
    stars = max(0, int(stars or 0))
    if stars == 0:
        return 0.0
    return round(min(100.0, 100.0 * math.log10(stars + 10) / math.log10(200010)), 2)


def _maintenance_score(stars: int, forks: int, open_issues: int, archived: bool) -> float:
    if archived:
        return 0.0
    stars = max(1, int(stars or 0))
    forks = max(0, int(forks or 0))
    open_issues = max(0, int(open_issues or 0))
    ratio = open_issues / stars
    if ratio <= 0.005:
        score = 100.0
    elif ratio <= 0.02:
        score = 86.0
    elif ratio <= 0.05:
        score = 72.0
    elif ratio <= 0.10:
        score = 58.0
    else:
        score = 42.0
    if forks >= max(10, int(stars * 0.03)):
        score = min(100.0, score + 5.0)
    return score


def _text_tokens(repo: dict) -> str:
    topics = " ".join(repo.get("topics") or [])
    return " ".join(
        filter(
            None,
            [
                repo.get("name"),
                repo.get("description"),
                topics,
                repo.get("homepage"),
            ],
        )
    ).lower()


def _automation_fit(repo: dict, seed: dict | None) -> float:
    if seed and seed.get("automation_fit") is not None:
        return float(seed["automation_fit"])
    hay = _text_tokens(repo)
    hits = sum(1 for term in AUTOMATION_TERMS if term in hay)
    if hits >= 5:
        return 100.0
    if hits >= 3:
        return 88.0
    if hits >= 2:
        return 76.0
    if hits == 1:
        return 58.0
    return 30.0


def infer_commercial_model(repo: dict) -> str:
    hay = _text_tokens(repo)
    signals = []
    if "self-host" in hay or "self hosted" in hay:
        signals.append("self-hosted")
    if "cloud" in hay or "hosted" in hay or "saas" in hay:
        signals.append("hosted SaaS")
    if "enterprise" in hay:
        signals.append("enterprise")
    if re.search(r"\bapi\b", hay):
        signals.append("API")
    if "embedded" in hay:
        signals.append("embedded")
    if "newsletter" in hay or "mailing list" in hay:
        signals.append("newsletter")
    if "social media" in hay or "social-media" in hay:
        signals.append("social distribution")
    if "monitor" in hay or "tracking" in hay:
        signals.append("monitoring")
    return " / ".join(dict.fromkeys(signals)) if signals else "UNKNOWN"


def _monetization_score(repo: dict, commercial_model: str, seed: dict | None) -> float:
    if seed and seed.get("commercial_model"):
        return 95.0
    hay = _text_tokens(repo)
    hits = sum(1 for term in MONETIZATION_TERMS if term in hay)
    if commercial_model != "UNKNOWN":
        hits += 1
    if hits >= 4:
        return 95.0
    if hits >= 2:
        return 82.0
    if hits == 1:
        return 66.0
    return 35.0


def infer_provider_role(repo: dict) -> str:
    hay = _text_tokens(repo)
    if "newsletter" in hay or "mailing list" in hay:
        return "NEWSLETTER"
    if "social media" in hay or "social-media" in hay:
        return "DISTRIBUTION"
    if "change detection" in hay or "monitoring" in hay or "price tracker" in hay:
        return "MONITORING"
    if "browser automation" in hay or "browser agent" in hay:
        return "BROWSER_AUTOMATION"
    if "crawler" in hay or "scraper" in hay or "rss" in hay:
        return "DATA_COLLECTION"
    if "agent workflow" in hay or "agentic workflow" in hay:
        return "AGENT_WORKFLOW"
    if "ai application" in hay or "rag" in hay:
        return "AI_APP_PLATFORM"
    if "automation" in hay or "workflow" in hay or "integration" in hay:
        return "AUTOMATION"
    return "OTHER"


def evaluate_repository(repo: dict, seed: dict | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    stars = int(repo.get("stargazers_count") or 0)
    forks = int(repo.get("forks_count") or repo.get("forks") or 0)
    open_issues = int(repo.get("open_issues_count") or repo.get("open_issues") or 0)
    archived = bool(repo.get("archived"))
    pushed_at = _parse_time(repo.get("pushed_at"))
    license_data = repo.get("license") or {}
    spdx = license_data.get("spdx_id") if isinstance(license_data, dict) else None

    license_policy = seed.get("license_policy") if seed else license_policy_for(spdx)
    license_scores = {
        "PERMISSIVE": 100.0,
        "WEAK_COPYLEFT": 82.0,
        "COPYLEFT": 68.0,
        "CONDITIONAL": 58.0,
        "UNKNOWN": 35.0,
        "RESTRICTED": 5.0,
    }
    commercial_model = (
        seed.get("commercial_model")
        if seed and seed.get("commercial_model")
        else infer_commercial_model(repo)
    )
    commercial_fit = (
        seed.get("commercial_fit")
        if seed and seed.get("commercial_fit")
        else {
            "PERMISSIVE": "COMMERCIAL_ALLOWED",
            "WEAK_COPYLEFT": "COMPLIANCE_REQUIRED",
            "COPYLEFT": "COPYLEFT_COMPLIANCE_REQUIRED",
            "CONDITIONAL": "LICENSE_REVIEW_REQUIRED",
            "UNKNOWN": "LICENSE_REVIEW_REQUIRED",
            "RESTRICTED": "NOT_ELIGIBLE",
        }[license_policy]
    )

    components = {
        "license_score": license_scores[license_policy],
        "activity_score": _activity_score(pushed_at, now),
        "popularity_score": _popularity_score(stars),
        "maintenance_score": _maintenance_score(stars, forks, open_issues, archived),
        "automation_fit_score": _automation_fit(repo, seed),
        "monetization_score": _monetization_score(repo, commercial_model, seed),
    }
    score = round(
        components["license_score"] * 0.30
        + components["activity_score"] * 0.20
        + components["popularity_score"] * 0.15
        + components["maintenance_score"] * 0.10
        + components["automation_fit_score"] * 0.15
        + components["monetization_score"] * 0.10,
        2,
    )

    threshold = float(settings.side_business_build_ready_score)
    if archived or license_policy == "RESTRICTED":
        readiness = "BLOCKED"
    elif license_policy == "UNKNOWN":
        readiness = "RESEARCH"
    elif (
        score >= threshold
        and components["activity_score"] >= 50.0
        and components["automation_fit_score"] >= 60.0
        and components["monetization_score"] >= 60.0
    ):
        readiness = "BUILD_READY"
    elif score >= 55.0:
        readiness = "WATCH"
    else:
        readiness = "RESEARCH"

    role = seed.get("provider_role") if seed else infer_provider_role(repo)
    return {
        "provider_role": role or "OTHER",
        "license_spdx": spdx,
        "license_policy": license_policy,
        "license_notes": seed.get("license_notes") if seed else None,
        "commercial_model": commercial_model,
        "commercial_fit": commercial_fit,
        "stars": stars,
        "forks": forks,
        "open_issues": open_issues,
        "pushed_at": pushed_at,
        "archived": archived,
        **components,
        "score": score,
        "readiness": readiness,
    }


class GitHubProviderClient:
    def __init__(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": settings.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        self.client = httpx.Client(
            base_url="https://api.github.com",
            headers=headers,
            timeout=settings.request_timeout_seconds,
        )

    def close(self) -> None:
        self.client.close()

    def repository(self, repo_full_name: str) -> dict:
        response = self.client.get(f"/repos/{repo_full_name}")
        response.raise_for_status()
        return response.json()

    def search(self, query: str, per_page: int) -> list[dict]:
        response = self.client.get(
            "/search/repositories",
            params={
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": max(1, min(int(per_page), 30)),
            },
        )
        response.raise_for_status()
        return response.json().get("items", [])


def _save_provider(repo: dict, origin: str, seed: dict | None) -> dict:
    evaluation = evaluate_repository(repo, seed=seed)
    full_name = repo["full_name"]
    repo_url = repo.get("html_url") or f"https://github.com/{full_name}"
    metadata = {
        "topics": repo.get("topics") or [],
        "default_branch": repo.get("default_branch"),
        "watchers_count": repo.get("watchers_count"),
        "subscribers_count": repo.get("subscribers_count"),
        "network_count": repo.get("network_count"),
    }
    params = {
        "repo_full_name": full_name,
        "github_repo_id": repo.get("id"),
        "repo_url": repo_url,
        "name": repo.get("name") or full_name.rsplit("/", 1)[-1],
        "provider_role": evaluation["provider_role"],
        "origin": "SEED" if seed else origin,
        "description": repo.get("description"),
        "homepage": repo.get("homepage"),
        "language": repo.get("language"),
        "license_spdx": evaluation["license_spdx"],
        "license_policy": evaluation["license_policy"],
        "license_notes": evaluation["license_notes"],
        "commercial_model": evaluation["commercial_model"],
        "commercial_fit": evaluation["commercial_fit"],
        "stars": evaluation["stars"],
        "forks": evaluation["forks"],
        "open_issues": evaluation["open_issues"],
        "pushed_at": evaluation["pushed_at"],
        "archived": evaluation["archived"],
        "license_score": evaluation["license_score"],
        "activity_score": evaluation["activity_score"],
        "popularity_score": evaluation["popularity_score"],
        "maintenance_score": evaluation["maintenance_score"],
        "automation_fit_score": evaluation["automation_fit_score"],
        "monetization_score": evaluation["monetization_score"],
        "score": evaluation["score"],
        "readiness": evaluation["readiness"],
        "metadata": json.dumps(metadata, ensure_ascii=False),
    }

    with engine.begin() as db:
        previous = db.execute(
            text("""
              SELECT id,readiness
              FROM side_business_providers
              WHERE repo_full_name=:repo_full_name
              FOR UPDATE
            """),
            {"repo_full_name": full_name},
        ).mappings().one_or_none()

        provider_id = db.execute(
            text("""
              INSERT INTO side_business_providers(
                repo_full_name,github_repo_id,repo_url,name,provider_role,discovery_origin,
                description,homepage,language,license_spdx,license_policy,license_notes,
                commercial_model,commercial_fit,stars,forks,open_issues,pushed_at,archived,
                license_score,activity_score,popularity_score,maintenance_score,
                automation_fit_score,monetization_score,score,readiness,metadata,
                last_discovered_at,last_scored_at,updated_at)
              VALUES(
                :repo_full_name,:github_repo_id,:repo_url,:name,:provider_role,:origin,
                :description,:homepage,:language,:license_spdx,:license_policy,:license_notes,
                :commercial_model,:commercial_fit,:stars,:forks,:open_issues,:pushed_at,:archived,
                :license_score,:activity_score,:popularity_score,:maintenance_score,
                :automation_fit_score,:monetization_score,:score,:readiness,CAST(:metadata AS jsonb),
                now(),now(),now())
              ON CONFLICT(repo_full_name) DO UPDATE SET
                github_repo_id=excluded.github_repo_id,
                repo_url=excluded.repo_url,
                name=excluded.name,
                provider_role=excluded.provider_role,
                discovery_origin=CASE
                  WHEN side_business_providers.discovery_origin='SEED' THEN 'SEED'
                  ELSE excluded.discovery_origin
                END,
                description=excluded.description,
                homepage=excluded.homepage,
                language=excluded.language,
                license_spdx=excluded.license_spdx,
                license_policy=excluded.license_policy,
                license_notes=COALESCE(excluded.license_notes,side_business_providers.license_notes),
                commercial_model=excluded.commercial_model,
                commercial_fit=excluded.commercial_fit,
                stars=excluded.stars,
                forks=excluded.forks,
                open_issues=excluded.open_issues,
                pushed_at=excluded.pushed_at,
                archived=excluded.archived,
                license_score=excluded.license_score,
                activity_score=excluded.activity_score,
                popularity_score=excluded.popularity_score,
                maintenance_score=excluded.maintenance_score,
                automation_fit_score=excluded.automation_fit_score,
                monetization_score=excluded.monetization_score,
                score=excluded.score,
                readiness=excluded.readiness,
                active=true,
                metadata=excluded.metadata,
                last_discovered_at=now(),
                last_scored_at=now(),
                updated_at=now()
              RETURNING id
            """),
            params,
        ).scalar_one()

        db.execute(
            text("""
              INSERT INTO side_business_provider_snapshots(
                provider_id,stars,forks,open_issues,pushed_at,archived,
                license_spdx,license_policy,commercial_model,
                license_score,activity_score,popularity_score,maintenance_score,
                automation_fit_score,monetization_score,score,readiness,metadata)
              VALUES(
                :provider_id,:stars,:forks,:open_issues,:pushed_at,:archived,
                :license_spdx,:license_policy,:commercial_model,
                :license_score,:activity_score,:popularity_score,:maintenance_score,
                :automation_fit_score,:monetization_score,:score,:readiness,CAST(:metadata AS jsonb))
            """),
            {"provider_id": provider_id, **params},
        )

        promoted = bool(previous and previous["readiness"] != "BUILD_READY" and evaluation["readiness"] == "BUILD_READY")
        if previous is None and evaluation["readiness"] == "BUILD_READY":
            promoted = True
        downgraded = bool(previous and previous["readiness"] == "BUILD_READY" and evaluation["readiness"] != "BUILD_READY")

        if evaluation["readiness"] == "BUILD_READY":
            reason = (
                f"score={evaluation['score']:.2f}; "
                f"license={evaluation['license_policy']}; "
                f"commercial_fit={evaluation['commercial_fit']}"
            )
            db.execute(
                text("""
                  INSERT INTO side_business_build_queue(
                    provider_id,queue_status,provider_score,reason,queued_at,updated_at)
                  VALUES(:provider_id,'QUEUED',:score,:reason,now(),now())
                  ON CONFLICT(provider_id) DO UPDATE SET
                    queue_status='QUEUED',
                    provider_score=excluded.provider_score,
                    reason=excluded.reason,
                    queued_at=CASE
                      WHEN side_business_build_queue.queue_status='QUEUED'
                        THEN side_business_build_queue.queued_at
                      ELSE now()
                    END,
                    updated_at=now()
                """),
                {"provider_id": provider_id, "score": evaluation["score"], "reason": reason},
            )
        else:
            db.execute(
                text("""
                  UPDATE side_business_build_queue
                  SET queue_status='STALE',
                      provider_score=:score,
                      reason=:reason,
                      updated_at=now()
                  WHERE provider_id=:provider_id
                    AND queue_status<>'STALE'
                """),
                {
                    "provider_id": provider_id,
                    "score": evaluation["score"],
                    "reason": f"readiness={evaluation['readiness']}; automatic re-evaluation removed eligibility",
                },
            )

    return {
        "provider_id": str(provider_id),
        "repo_full_name": full_name,
        "score": evaluation["score"],
        "readiness": evaluation["readiness"],
        "promoted": promoted,
        "downgraded": downgraded,
    }


def _existing_repositories(limit: int, exclude: set[str]) -> list[str]:
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT repo_full_name
              FROM side_business_providers
              WHERE active=true
              ORDER BY COALESCE(last_scored_at,to_timestamp(0)) ASC,updated_at ASC
              LIMIT :limit
            """),
            {"limit": max(1, min(int(limit), 100))},
        ).all()
    return [row[0] for row in rows if row[0].lower() not in exclude]


def run_side_business_registry_cycle() -> dict:
    with engine.begin() as db:
        run_id = db.execute(
            text("INSERT INTO side_business_registry_runs DEFAULT VALUES RETURNING id")
        ).scalar_one()

    if not settings.side_business_registry_enabled:
        with engine.begin() as db:
            db.execute(
                text("""
                  UPDATE side_business_registry_runs
                  SET status='SKIPPED',finished_at=now()
                  WHERE id=:id
                """),
                {"id": run_id},
            )
        return {"run_id": str(run_id), "status": "SKIPPED"}

    counters = {
        "seeds_checked": 0,
        "discovered": 0,
        "refreshed": 0,
        "queued": 0,
        "promoted": 0,
        "downgraded": 0,
    }
    errors: list[str] = []
    seen: set[str] = set()
    client = GitHubProviderClient()

    def process(repo: dict, origin: str) -> None:
        full_name = (repo.get("full_name") or "").strip()
        if not full_name:
            return
        key = full_name.lower()
        if key in seen:
            return
        seen.add(key)
        seed = SEED_BY_REPO.get(key)
        result = _save_provider(repo, origin=origin, seed=seed)
        counters["refreshed"] += 1
        counters["promoted"] += int(result["promoted"])
        counters["downgraded"] += int(result["downgraded"])
        counters["queued"] += int(result["promoted"])

    try:
        for seed in SEED_PROVIDERS:
            counters["seeds_checked"] += 1
            try:
                process(client.repository(seed["repo_full_name"]), "SEED")
            except Exception as exc:
                errors.append(f"seed {seed['repo_full_name']}: {exc}")

        if settings.side_business_discovery_enabled:
            freshness = (datetime.now(timezone.utc) - timedelta(days=180)).date().isoformat()
            for template in DISCOVERY_QUERY_TEMPLATES:
                query = f"{template} pushed:>={freshness}"
                try:
                    results = client.search(query, settings.side_business_results_per_query)
                except Exception as exc:
                    errors.append(f"search {template}: {exc}")
                    continue
                for repo in results:
                    if len(seen) >= settings.side_business_max_candidates_per_run:
                        break
                    before = len(seen)
                    try:
                        process(repo, "DISCOVERED")
                    except Exception as exc:
                        errors.append(f"repo {repo.get('full_name')}: {exc}")
                    if len(seen) > before:
                        counters["discovered"] += 1
                if len(seen) >= settings.side_business_max_candidates_per_run:
                    break

        remaining = max(0, settings.side_business_max_candidates_per_run - len(seen))
        if remaining:
            for full_name in _existing_repositories(remaining, seen):
                try:
                    process(client.repository(full_name), "DISCOVERED")
                except Exception as exc:
                    errors.append(f"refresh {full_name}: {exc}")
    finally:
        client.close()

    with engine.connect() as db:
        build_ready = int(
            db.execute(
                text("""
                  SELECT COUNT(*)
                  FROM side_business_providers
                  WHERE active=true AND readiness='BUILD_READY'
                """)
            ).scalar_one()
        )

    status = "SUCCESS"
    if errors and counters["refreshed"]:
        status = "PARTIAL"
    elif errors and not counters["refreshed"]:
        status = "FAILED"

    with engine.begin() as db:
        db.execute(
            text("""
              UPDATE side_business_registry_runs
              SET status=:status,
                  seeds_checked=:seeds_checked,
                  discovered=:discovered,
                  refreshed=:refreshed,
                  build_ready=:build_ready,
                  queued=:queued,
                  promoted=:promoted,
                  downgraded=:downgraded,
                  errors=:errors,
                  error_summary=:error_summary,
                  finished_at=now()
              WHERE id=:id
            """),
            {
                "id": run_id,
                "status": status,
                **counters,
                "build_ready": build_ready,
                "errors": len(errors),
                "error_summary": "\n".join(errors[:20])[:8000] if errors else None,
            },
        )

    return {
        "run_id": str(run_id),
        "status": status,
        **counters,
        "build_ready": build_ready,
        "errors": len(errors),
    }


def list_side_business_providers(limit: int = 100, readiness: str | None = None) -> list[dict]:
    normalized = readiness.upper().strip() if readiness else None
    allowed = {"RESEARCH", "WATCH", "BUILD_READY", "BLOCKED"}
    if normalized and normalized not in allowed:
        raise ValueError("readiness must be RESEARCH, WATCH, BUILD_READY, or BLOCKED")
    sql = """
      SELECT id,repo_full_name,repo_url,name,provider_role,discovery_origin,
             description,homepage,language,license_spdx,license_policy,license_notes,
             commercial_model,commercial_fit,stars,forks,open_issues,pushed_at,archived,
             license_score,activity_score,popularity_score,maintenance_score,
             automation_fit_score,monetization_score,score,readiness,
             last_discovered_at,last_scored_at,created_at,updated_at
      FROM side_business_providers
      WHERE active=true
    """
    params = {"limit": max(1, min(int(limit), 500))}
    if normalized:
        sql += " AND readiness=:readiness"
        params["readiness"] = normalized
    sql += " ORDER BY score DESC,stars DESC,repo_full_name ASC LIMIT :limit"
    with engine.connect() as db:
        return [dict(row) for row in db.execute(text(sql), params).mappings().all()]


def list_side_business_build_queue(limit: int = 100) -> list[dict]:
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT q.id,q.provider_id,p.repo_full_name,p.repo_url,p.provider_role,
                     p.license_policy,p.commercial_model,p.commercial_fit,p.readiness,
                     q.queue_status,q.provider_score,q.reason,q.queued_at,q.updated_at
              FROM side_business_build_queue q
              JOIN side_business_providers p ON p.id=q.provider_id
              ORDER BY
                CASE WHEN q.queue_status='QUEUED' THEN 0 ELSE 1 END,
                q.provider_score DESC,q.updated_at DESC
              LIMIT :limit
            """),
            {"limit": max(1, min(int(limit), 500))},
        ).mappings().all()
    return [dict(row) for row in rows]


def list_side_business_registry_runs(limit: int = 30) -> list[dict]:
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT id,status,seeds_checked,discovered,refreshed,build_ready,
                     queued,promoted,downgraded,errors,error_summary,started_at,finished_at
              FROM side_business_registry_runs
              ORDER BY started_at DESC
              LIMIT :limit
            """),
            {"limit": max(1, min(int(limit), 200))},
        ).mappings().all()
    return [dict(row) for row in rows]


def build_ready_provider_fingerprint(repo_full_name: str) -> str:
    return hashlib.sha256(
        ("side-business-provider:" + repo_full_name.lower()).encode("utf-8")
    ).hexdigest()

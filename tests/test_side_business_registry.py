from datetime import datetime, timedelta, timezone

from app.side_business_registry import (
    SEED_PROVIDERS,
    evaluate_repository,
    license_policy_for,
)


def _repo(
    *,
    full_name="example/automation",
    stars=50000,
    forks=5000,
    open_issues=120,
    pushed_at=None,
    archived=False,
    spdx="MIT",
    description="Self-hosted AI workflow automation platform with API, cloud and enterprise support.",
    topics=None,
):
    now = datetime.now(timezone.utc)
    return {
        "id": 123,
        "full_name": full_name,
        "name": full_name.rsplit("/", 1)[-1],
        "html_url": f"https://github.com/{full_name}",
        "description": description,
        "topics": topics or ["automation", "workflow", "ai", "self-hosted", "api"],
        "homepage": "https://example.com",
        "language": "Python",
        "stargazers_count": stars,
        "forks_count": forks,
        "open_issues_count": open_issues,
        "pushed_at": pushed_at or (now - timedelta(days=3)).isoformat(),
        "archived": archived,
        "license": {"spdx_id": spdx} if spdx is not None else None,
    }


def test_seed_registry_contains_exactly_twelve_unique_projects():
    repos = [item["repo_full_name"] for item in SEED_PROVIDERS]
    assert len(repos) == 12
    assert len(set(repos)) == 12
    assert set(repos) == {
        "n8n-io/n8n",
        "activepieces/activepieces",
        "langgenius/dify",
        "langflow-ai/langflow",
        "gitroomhq/postiz-app",
        "knadh/listmonk",
        "unclecode/crawl4ai",
        "apify/crawlee",
        "dgtlmoon/changedetection.io",
        "browser-use/browser-use",
        "DIYgod/RSSHub",
        "bytechefhq/bytechef",
    }


def test_license_policy_classification():
    assert license_policy_for("MIT") == "PERMISSIVE"
    assert license_policy_for("Apache-2.0") == "PERMISSIVE"
    assert license_policy_for("MPL-2.0") == "WEAK_COPYLEFT"
    assert license_policy_for("AGPL-3.0") == "COPYLEFT"
    assert license_policy_for("NOASSERTION") == "UNKNOWN"
    assert license_policy_for(None) == "UNKNOWN"


def test_active_permissive_repository_can_be_build_ready():
    now = datetime.now(timezone.utc)
    result = evaluate_repository(_repo(), now=now)
    assert result["license_policy"] == "PERMISSIVE"
    assert result["score"] >= 75
    assert result["readiness"] == "BUILD_READY"


def test_unknown_license_is_never_auto_promoted():
    now = datetime.now(timezone.utc)
    result = evaluate_repository(
        _repo(spdx="NOASSERTION", stars=200000, forks=50000, open_issues=100),
        now=now,
    )
    assert result["score"] >= 70
    assert result["license_policy"] == "UNKNOWN"
    assert result["readiness"] == "RESEARCH"


def test_archived_repository_is_blocked_even_with_good_metrics():
    now = datetime.now(timezone.utc)
    result = evaluate_repository(_repo(archived=True), now=now)
    assert result["readiness"] == "BLOCKED"


def test_seed_license_override_preserves_known_commercial_constraints():
    now = datetime.now(timezone.utc)
    seed = next(item for item in SEED_PROVIDERS if item["repo_full_name"] == "n8n-io/n8n")
    result = evaluate_repository(
        _repo(
            full_name="n8n-io/n8n",
            stars=200000,
            forks=60000,
            open_issues=1000,
            spdx="NOASSERTION",
        ),
        seed=seed,
        now=now,
    )
    assert result["license_policy"] == "CONDITIONAL"
    assert result["commercial_fit"] == "INTERNAL_USE_RECOMMENDED"
    assert result["readiness"] == "BUILD_READY"


def test_stale_project_loses_activity_and_cannot_auto_promote():
    now = datetime.now(timezone.utc)
    result = evaluate_repository(
        _repo(pushed_at=(now - timedelta(days=500)).isoformat(), stars=2000),
        now=now,
    )
    assert result["activity_score"] < 50
    assert result["readiness"] != "BUILD_READY"

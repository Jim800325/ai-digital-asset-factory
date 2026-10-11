from __future__ import annotations


def build_workbench_actions(metrics: dict) -> list[dict]:
    """Build deterministic, read-only next actions for the side-business workbench."""
    actions: list[dict] = []

    evidence_blocked = int(metrics.get("evidence_blocked") or 0)
    candidate_count = int(metrics.get("candidate") or 0)
    reports_missing = int(metrics.get("candidate_reports_missing") or 0)
    validations_missing = int(metrics.get("candidate_validations_missing") or 0)
    build_approval_pending = int(metrics.get("build_approval_pending") or 0)
    release_review_pending = int(metrics.get("release_review_pending") or 0)

    if evidence_blocked:
        actions.append(
            {
                "id": "expand-evidence",
                "priority": "HIGH",
                "type": "EVIDENCE_EXPANSION",
                "title": "補充多來源 Evidence",
                "count": evidence_blocked,
                "reason": (
                    f"{evidence_blocked} 個機會仍未通過 Evidence Gate；"
                    "優先為高分機會尋找第二個獨立來源。"
                ),
                "href": "/opportunities?gate=blocked",
            }
        )

    if candidate_count and reports_missing:
        actions.append(
            {
                "id": "generate-research",
                "priority": "HIGH",
                "type": "RESEARCH_REPORT",
                "title": "完成候選機會研究報告",
                "count": reports_missing,
                "reason": f"{reports_missing} 個 CANDIDATE 尚未有 CURRENT Research Report。",
                "href": "/research?stage=candidate",
            }
        )

    if candidate_count and validations_missing:
        actions.append(
            {
                "id": "validate-research",
                "priority": "HIGH",
                "type": "RESEARCH_VALIDATION",
                "title": "完成商業驗證",
                "count": validations_missing,
                "reason": (
                    f"{validations_missing} 個候選機會仍需 Buyer / Competitor / Pricing / "
                    "Willingness-to-Pay / Market Gap 驗證。"
                ),
                "href": "/research?stage=validating",
            }
        )

    if build_approval_pending:
        actions.append(
            {
                "id": "review-build-proposals",
                "priority": "ACTION_REQUIRED",
                "type": "BUILD_APPROVAL",
                "title": "審核 BUILD_READY Proposal",
                "count": build_approval_pending,
                "reason": f"{build_approval_pending} 個 Build Proposal 等待人工 APPROVE / REJECT。",
                "href": "/build-ready?status=pending",
            }
        )

    if release_review_pending:
        actions.append(
            {
                "id": "review-release",
                "priority": "ACTION_REQUIRED",
                "type": "RELEASE_REVIEW",
                "title": "處理 Release Review",
                "count": release_review_pending,
                "reason": f"{release_review_pending} 個真實 Release Candidate 等待人工審核。",
                "href": "/review",
            }
        )

    if not actions:
        actions.append(
            {
                "id": "run-pipeline",
                "priority": "INFO",
                "type": "PIPELINE_RUN",
                "title": "執行下一次 Pipeline",
                "count": 0,
                "reason": "目前沒有待處理業務 Gate；可執行新一輪機會發現。",
                "href": "/manual-pipeline",
            }
        )

    return actions

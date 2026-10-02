from __future__ import annotations

import json
import os
import sys
from uuid import UUID

from app.config import settings
from app.migrate import migration_files, migration_status
from app.vercel_prepare_acceptance import recover_prepare_acceptance


RUN_ID = UUID("ee4cdb3c-56ac-5b72-9f05-2f9d2db99168")
EXPECTED_PROJECT_ID = "prj_aXnSD2RqOV6gJgOmhUXWA4DFFdyJ"
EXPECTED_TEAM_ID = "team_JO3GTfLCviMWb2pAvSClH0iK"
REAL_PRODUCTION_PROJECT_ID = "prj_orLCRCIm7aVfImH8ihB3gponFOEl"
EXPECTED_BRANCH = (
    "feature/controlled-production-release-executor-v0.1-live-prepare-acceptance"
)


def _fail(message: str, *, code: int = 41) -> None:
    print(
        "STEP4A_BUILD_RECOVERY_ERROR="
        + json.dumps({"message": message}, sort_keys=True)
    )
    raise SystemExit(code)


def _safe_result(result: dict) -> dict:
    return {
        "acceptance_status": result.get("acceptance_status"),
        "execution_id": str(result.get("execution_id") or ""),
        "prepare_write_count": int(result.get("prepare_write_count") or 0),
        "candidate_vercel_deployment_id": result.get(
            "candidate_vercel_deployment_id"
        ),
        "production_vercel_deployment_id": result.get(
            "production_vercel_deployment_id"
        ),
        "previous_production_deployment_id": result.get(
            "previous_production_deployment_id"
        ),
        "provider_recovery_mode": "GET_ONLY",
        "provider_write_performed_by_recovery": False,
        "production_traffic_changed": False,
        "production_promotion_performed": False,
        "production_rollback_performed": False,
    }


def main() -> None:
    if os.getenv("VERCEL_ENV", "").strip().lower() != "preview":
        print("STEP4A_BUILD_RECOVERY=SKIPPED_NON_PREVIEW")
        return

    ref = os.getenv("VERCEL_GIT_COMMIT_REF", "").strip()
    if ref and ref != EXPECTED_BRANCH:
        print("STEP4A_BUILD_RECOVERY=SKIPPED_OTHER_BRANCH")
        return

    if settings.production_execution_preview_only is not True:
        _fail("production_execution_preview_only must remain true")
    if settings.controlled_production_executor_enabled is not True:
        _fail("controlled production executor is not enabled")
    if settings.production_execution_adapter.strip().upper() != (
        "VERCEL_CONTROLLED_EXECUTOR"
    ):
        _fail("production execution adapter is not VERCEL_CONTROLLED_EXECUTOR")
    if settings.production_promotion_enabled:
        _fail("production promotion must remain disabled")
    if settings.production_rollback_enabled:
        _fail("production rollback must remain disabled")

    allowed_projects = settings.production_execution_allowed_project_id_list
    allowed_teams = settings.production_execution_allowed_team_id_list
    denied_projects = settings.production_execution_denied_project_id_list
    if allowed_projects != [EXPECTED_PROJECT_ID]:
        _fail("sacrificial project allowlist is not the exact expected project")
    if allowed_teams != [EXPECTED_TEAM_ID]:
        _fail("team allowlist is not the exact expected team")
    if REAL_PRODUCTION_PROJECT_ID not in denied_projects:
        _fail("real Production project is missing from denylist")
    if not settings.vercel_controlled_executor_token.strip():
        _fail("Vercel controlled executor token is unavailable")

    migrations = migration_status()
    expected_migrations = migration_files()
    if (
        "028_vercel_prepare_provider_id_recovery.sql"
        not in expected_migrations
        or migrations.get("status") != "CURRENT"
    ):
        _fail("migration 028 is missing or current migrations are not applied")

    result = recover_prepare_acceptance(RUN_ID)
    safe = _safe_result(result)
    print("STEP4A_BUILD_RECOVERY_RESULT=" + json.dumps(safe, sort_keys=True))

    if safe["prepare_write_count"] != 1:
        _fail("recovery did not preserve exactly one PREPARE provider write")
    if safe["production_vercel_deployment_id"] is not None:
        _fail("Production deployment pointer changed during recovery")
    if safe["previous_production_deployment_id"] is not None:
        _fail("previous Production deployment pointer changed during recovery")
    if safe["acceptance_status"] not in {
        "READY_FOR_PROMOTION",
        "PROMOTE_AUTHORIZED",
        "CLEANED_UP",
    }:
        _fail(
            "recovery did not converge to a terminal safe acceptance state",
            code=42,
        )


if __name__ == "__main__":
    main()

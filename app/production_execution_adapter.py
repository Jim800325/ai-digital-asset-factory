from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import settings


REAL_PRODUCTION_PROJECT_ID = "prj_orLCRCIm7aVfImH8ihB3gponFOEl"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class CandidateDeployment:
    deployment_id: str
    url: str
    state: str
    provider_write_performed: bool
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PreparedDeploymentRequest:
    deployment_id: str
    project_id: str
    team_id: str
    body: dict[str, Any]
    request_sha256: str


class ProviderWriteOutcomeUnknown(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        deployment_id: str,
        error_type: str,
        evidence_sha256: str,
    ) -> None:
        super().__init__(message)
        self.deployment_id = deployment_id
        self.error_type = error_type
        self.evidence_sha256 = evidence_sha256


class ProviderPrepareRejected(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        evidence_sha256: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.evidence_sha256 = evidence_sha256


class ProductionExecutionAdapter(Protocol):
    kind: str

    def prepare_candidate(
        self,
        execution_snapshot: dict[str, Any],
    ) -> CandidateDeployment:
        ...

    def read_candidate(
        self,
        execution_snapshot: dict[str, Any],
        deployment_id: str,
    ) -> CandidateDeployment | None:
        ...

    def promote(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def rollback(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class MockProductionExecutionAdapter:
    kind = "MOCK"

    def prepare_candidate(
        self,
        execution_snapshot: dict[str, Any],
    ) -> CandidateDeployment:
        execution_sha = str(
            execution_snapshot.get("execution_sha256") or ""
        ).strip().lower()
        if len(execution_sha) != 64:
            raise RuntimeError(
                "MOCK prepare requires a valid immutable execution SHA-256"
            )

        token = hashlib.sha256(
            ("mock-production-candidate|" + execution_sha).encode("utf-8")
        ).hexdigest()[:24]
        deployment_id = "mock_dpl_" + token
        return CandidateDeployment(
            deployment_id=deployment_id,
            url=f"https://{deployment_id}.mock.invalid",
            state="READY",
            provider_write_performed=False,
            metadata={
                "adapter": self.kind,
                "source": "DETERMINISTIC_MOCK",
                "external_side_effects": "DENY",
                "production_traffic_changed": False,
            },
        )

    def read_candidate(
        self,
        execution_snapshot: dict[str, Any],
        deployment_id: str,
    ) -> CandidateDeployment | None:
        candidate = self.prepare_candidate(execution_snapshot)
        if deployment_id != candidate.deployment_id:
            return None
        return CandidateDeployment(
            deployment_id=candidate.deployment_id,
            url=candidate.url,
            state=candidate.state,
            provider_write_performed=False,
            metadata={
                **candidate.metadata,
                "source": "DETERMINISTIC_MOCK_READ",
            },
        )

    def promote(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError(
            "MOCK Step 4 does not implement Production promotion"
        )

    def rollback(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError(
            "MOCK Step 4 does not implement Production rollback"
        )


class VercelControlledExecutionAdapter:
    kind = "VERCEL_CONTROLLED_EXECUTOR"

    def __init__(
        self,
        *,
        token: str | None = None,
        allowed_project_ids: list[str] | None = None,
        allowed_team_ids: list[str] | None = None,
        denied_project_ids: list[str] | None = None,
        api_base: str | None = None,
        timeout_seconds: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.token = (
            token
            if token is not None
            else settings.vercel_controlled_executor_token
        ).strip()
        self.allowed_project_ids = set(
            allowed_project_ids
            if allowed_project_ids is not None
            else settings.production_execution_allowed_project_id_list
        )
        self.allowed_team_ids = set(
            allowed_team_ids
            if allowed_team_ids is not None
            else settings.production_execution_allowed_team_id_list
        )
        configured_denied = set(
            denied_project_ids
            if denied_project_ids is not None
            else settings.production_execution_denied_project_id_list
        )
        self.denied_project_ids = configured_denied | {
            REAL_PRODUCTION_PROJECT_ID
        }
        self.api_base = (
            api_base
            if api_base is not None
            else settings.vercel_controlled_executor_api_base
        ).rstrip("/")
        self.timeout_seconds = int(
            timeout_seconds
            if timeout_seconds is not None
            else settings.vercel_controlled_executor_timeout_seconds
        )
        self.client = client

    def _target(self, execution_snapshot: dict[str, Any]) -> tuple[str, str]:
        if execution_snapshot.get("target_provider") != "VERCEL":
            raise RuntimeError("Controlled Vercel PREPARE requires VERCEL target")
        if execution_snapshot.get("target_environment") != "production":
            raise RuntimeError(
                "Controlled Vercel PREPARE requires production configuration"
            )

        project_id = str(
            execution_snapshot.get("target_project_id") or ""
        ).strip()
        team_id = str(
            execution_snapshot.get("target_team_id") or ""
        ).strip()
        if not project_id or not team_id:
            raise RuntimeError(
                "Controlled Vercel PREPARE requires exact project and team IDs"
            )
        if project_id in self.denied_project_ids:
            raise RuntimeError(
                "Controlled Vercel PREPARE target is denylisted"
            )
        if not self.allowed_project_ids:
            raise RuntimeError(
                "Controlled Vercel PREPARE sacrificial project allowlist is empty"
            )
        if project_id not in self.allowed_project_ids:
            raise RuntimeError(
                "Controlled Vercel PREPARE target is not allowlisted"
            )
        if not self.allowed_team_ids:
            raise RuntimeError(
                "Controlled Vercel PREPARE team allowlist is empty"
            )
        if team_id not in self.allowed_team_ids:
            raise RuntimeError(
                "Controlled Vercel PREPARE team is not allowlisted"
            )
        if (
            settings.production_execution_preview_only
            and (os.getenv("VERCEL_ENV") or "").strip().lower() != "preview"
        ):
            raise RuntimeError(
                "Controlled Vercel PREPARE is Preview-runtime-only"
            )
        return project_id, team_id

    def validate_target(
        self,
        execution_snapshot: dict[str, Any],
    ) -> tuple[str, str]:
        return self._target(execution_snapshot)

    def probe_target(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, str]:
        project_id, team_id = self._target(execution_snapshot)
        project_name = self._project_name(project_id, team_id)
        return {
            "project_id": project_id,
            "team_id": team_id,
            "project_name": project_name,
            "provider_write_performed": False,
            "production_traffic_changed": False,
        }

    def _require_token(self) -> str:
        if not self.token:
            raise RuntimeError(
                "VERCEL_CONTROLLED_EXECUTOR_TOKEN is not configured"
            )
        return self.token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._require_token()}",
            "Content-Type": "application/json",
            "User-Agent": "ai-digital-asset-factory-controlled-executor/0.1",
        }

    def _client(self) -> tuple[httpx.Client, bool]:
        if self.client is not None:
            return self.client, False
        return httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=False,
        ), True

    @staticmethod
    def deterministic_deployment_id(execution_sha256: str) -> str:
        value = (execution_sha256 or "").strip().lower()
        if len(value) != 64:
            raise RuntimeError(
                "Controlled Vercel PREPARE requires execution SHA-256"
            )
        token = hashlib.sha256(
            ("vercel-controlled-prepare|" + value).encode("utf-8")
        ).hexdigest()[:32]
        return "dpl_" + token

    @staticmethod
    def _provider_state(payload: dict[str, Any]) -> str:
        return str(
            payload.get("readyState")
            or payload.get("status")
            or "UNKNOWN"
        ).strip().upper()

    @staticmethod
    def _response_evidence(
        *,
        status_code: int,
        text: str,
    ) -> str:
        return _sha256({
            "status_code": int(status_code),
            "body": (text or "")[:16000],
        })

    def _request_files(
        self,
        execution_snapshot: dict[str, Any],
    ) -> list[dict[str, str]]:
        bundle = execution_snapshot.get("execution_bundle")
        if not isinstance(bundle, dict):
            raise RuntimeError("Execution bundle is unavailable")
        artifacts = bundle.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise RuntimeError("Execution bundle contains no artifacts")

        result: list[dict[str, str]] = []
        seen: set[str] = set()
        total_bytes = 0
        for item in artifacts:
            if not isinstance(item, dict):
                raise RuntimeError("Execution artifact is invalid")
            path = str(item.get("relative_path") or "").strip()
            encoded = item.get("content_base64")
            if (
                not path
                or path.startswith("/")
                or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or path in seen
            ):
                raise RuntimeError("Execution artifact path is unsafe")
            if not isinstance(encoded, str) or not encoded:
                raise RuntimeError("Execution artifact bytes are unavailable")
            size = int(item.get("byte_size") or 0)
            if size < 0:
                raise RuntimeError("Execution artifact size is invalid")
            total_bytes += size
            if len(result) >= 100 or total_bytes > 10 * 1024 * 1024:
                raise RuntimeError(
                    "Controlled Vercel PREPARE artifact limit exceeded"
                )
            seen.add(path)
            result.append({
                "file": path,
                "data": encoded,
                "encoding": "base64",
            })
        return result

    def _project_name(self, project_id: str, team_id: str) -> str:
        client, owned = self._client()
        try:
            response = client.get(
                f"{self.api_base}/v9/projects/{project_id}",
                params={"teamId": team_id},
                headers=self._headers(),
            )
        finally:
            if owned:
                client.close()
        if not response.is_success:
            raise RuntimeError(
                "Controlled Vercel PREPARE project lookup failed: "
                f"HTTP {response.status_code}"
            )
        payload = response.json()
        if str(payload.get("id") or "") != project_id:
            raise RuntimeError("Vercel project lookup returned wrong project")
        account_id = str(
            payload.get("accountId")
            or payload.get("account_id")
            or ""
        )
        if account_id and account_id != team_id:
            raise RuntimeError("Vercel project lookup returned wrong team")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise RuntimeError("Vercel project lookup returned no project name")
        return name

    def build_prepare_request(
        self,
        execution_snapshot: dict[str, Any],
    ) -> PreparedDeploymentRequest:
        project_id, team_id = self._target(execution_snapshot)
        project_name = self._project_name(project_id, team_id)
        deployment_id = self.deterministic_deployment_id(
            str(execution_snapshot.get("execution_sha256") or "")
        )
        body = {
            "name": project_name,
            "project": project_id,
            "deploymentId": deployment_id,
            "files": self._request_files(execution_snapshot),
            "target": "production",
            "autoAssignCustomDomains": False,
            "meta": {
                "controlledExecutionId": str(
                    execution_snapshot.get("id") or ""
                ),
                "controlledExecutionSha256": str(
                    execution_snapshot.get("execution_sha256") or ""
                ),
                "controlledPlanSha256": str(
                    execution_snapshot.get("plan_sha256") or ""
                ),
                "controlledBundleSha256": str(
                    execution_snapshot.get("execution_bundle_sha256") or ""
                ),
                "controlledMode": "SACRIFICIAL_PREPARE_ONLY",
            },
        }
        return PreparedDeploymentRequest(
            deployment_id=deployment_id,
            project_id=project_id,
            team_id=team_id,
            body=body,
            request_sha256=_sha256({
                "team_id": team_id,
                "body": body,
            }),
        )

    def _candidate_from_payload(
        self,
        payload: dict[str, Any],
        *,
        expected_deployment_id: str,
        expected_project_id: str,
        expected_team_id: str,
        provider_write_performed: bool,
    ) -> CandidateDeployment:
        deployment_id = str(payload.get("id") or "").strip()
        if deployment_id != expected_deployment_id:
            raise RuntimeError(
                "Vercel returned an unexpected deployment ID"
            )

        project_id = str(
            payload.get("projectId")
            or payload.get("project_id")
            or expected_project_id
        ).strip()
        if project_id != expected_project_id:
            raise RuntimeError("Vercel deployment project mismatch")

        team_id = str(
            payload.get("teamId")
            or payload.get("team_id")
            or expected_team_id
        ).strip()
        if team_id != expected_team_id:
            raise RuntimeError("Vercel deployment team mismatch")

        aliases = payload.get("alias")
        if not isinstance(aliases, list):
            aliases = []
        alias_assigned = payload.get("aliasAssigned") is True
        if aliases or alias_assigned:
            raise RuntimeError(
                "Controlled Vercel PREPARE unexpectedly assigned an alias/domain"
            )

        url = str(payload.get("url") or "").strip()
        if not url:
            raise RuntimeError("Vercel deployment URL is unavailable")
        if not url.startswith("http"):
            url = "https://" + url

        state = self._provider_state(payload)
        return CandidateDeployment(
            deployment_id=deployment_id,
            url=url,
            state=state,
            provider_write_performed=provider_write_performed,
            metadata={
                "adapter": self.kind,
                "target_project_id": expected_project_id,
                "target_team_id": expected_team_id,
                "target": "production",
                "auto_assign_custom_domains": False,
                "alias_count": len(aliases),
                "alias_assigned": alias_assigned,
                "production_traffic_changed": False,
                "provider_state": state,
            },
        )

    def send_prepare(
        self,
        request: PreparedDeploymentRequest,
    ) -> CandidateDeployment:
        client, owned = self._client()
        try:
            try:
                response = client.post(
                    f"{self.api_base}/v13/deployments",
                    params={
                        "teamId": request.team_id,
                        "skipAutoDetectionConfirmation": "1",
                    },
                    headers=self._headers(),
                    json=request.body,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                evidence = _sha256({
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                    "deployment_id": request.deployment_id,
                })
                raise ProviderWriteOutcomeUnknown(
                    "Vercel PREPARE provider outcome is unknown",
                    deployment_id=request.deployment_id,
                    error_type=type(exc).__name__,
                    evidence_sha256=evidence,
                ) from exc
        finally:
            if owned:
                client.close()

        evidence_sha = self._response_evidence(
            status_code=response.status_code,
            text=response.text,
        )
        if response.status_code >= 500:
            raise ProviderWriteOutcomeUnknown(
                "Vercel PREPARE returned an ambiguous server error",
                deployment_id=request.deployment_id,
                error_type=f"HTTP_{response.status_code}",
                evidence_sha256=evidence_sha,
            )
        if not response.is_success:
            raise ProviderPrepareRejected(
                "Vercel PREPARE was rejected",
                status_code=response.status_code,
                evidence_sha256=evidence_sha,
            )

        payload = response.json()
        candidate = self._candidate_from_payload(
            payload,
            expected_deployment_id=request.deployment_id,
            expected_project_id=request.project_id,
            expected_team_id=request.team_id,
            provider_write_performed=True,
        )
        return CandidateDeployment(
            deployment_id=candidate.deployment_id,
            url=candidate.url,
            state=candidate.state,
            provider_write_performed=True,
            metadata={
                **candidate.metadata,
                "provider_result_sha256": evidence_sha,
                "prepare_request_sha256": request.request_sha256,
            },
        )

    def prepare_candidate(
        self,
        execution_snapshot: dict[str, Any],
    ) -> CandidateDeployment:
        request = self.build_prepare_request(execution_snapshot)
        return self.send_prepare(request)

    def read_candidate(
        self,
        execution_snapshot: dict[str, Any],
        deployment_id: str,
    ) -> CandidateDeployment | None:
        project_id, team_id = self._target(execution_snapshot)
        expected = self.deterministic_deployment_id(
            str(execution_snapshot.get("execution_sha256") or "")
        )
        if deployment_id != expected:
            raise RuntimeError(
                "Reconciliation deployment ID does not match immutable execution"
            )

        client, owned = self._client()
        try:
            response = client.get(
                f"{self.api_base}/v13/deployments/{deployment_id}",
                params={
                    "teamId": team_id,
                    "withGitRepoInfo": "true",
                },
                headers=self._headers(),
            )
        finally:
            if owned:
                client.close()

        if response.status_code == 404:
            return None
        if not response.is_success:
            raise RuntimeError(
                "Vercel PREPARE reconciliation read failed: "
                f"HTTP {response.status_code}"
            )

        payload = response.json()
        evidence_sha = self._response_evidence(
            status_code=response.status_code,
            text=response.text,
        )
        candidate = self._candidate_from_payload(
            payload,
            expected_deployment_id=deployment_id,
            expected_project_id=project_id,
            expected_team_id=team_id,
            provider_write_performed=False,
        )
        return CandidateDeployment(
            deployment_id=candidate.deployment_id,
            url=candidate.url,
            state=candidate.state,
            provider_write_performed=False,
            metadata={
                **candidate.metadata,
                "provider_result_sha256": evidence_sha,
                "source": "RECONCILIATION_READ",
            },
        )

    def promote(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError(
            "Step 4 PREPARE adapter does not implement Production promotion"
        )

    def rollback(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError(
            "Step 4 PREPARE adapter does not implement Production rollback"
        )


def get_production_execution_adapter(
    kind: str = "MOCK",
    **kwargs: Any,
) -> ProductionExecutionAdapter:
    normalized = (kind or "").strip().upper()
    if normalized == "MOCK":
        return MockProductionExecutionAdapter()
    if normalized == "VERCEL_CONTROLLED_EXECUTOR":
        return VercelControlledExecutionAdapter(**kwargs)
    raise RuntimeError(
        "Unsupported controlled Production execution adapter"
    )

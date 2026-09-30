from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CandidateDeployment:
    deployment_id: str
    url: str
    state: str
    provider_write_performed: bool
    metadata: dict[str, Any]


class ProductionExecutionAdapter(Protocol):
    kind: str

    def prepare_candidate(
        self,
        execution_snapshot: dict[str, Any],
    ) -> CandidateDeployment:
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

    def promote(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError(
            "MOCK Step 1 does not implement Production promotion"
        )

    def rollback(
        self,
        execution_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError(
            "MOCK Step 1 does not implement Production rollback"
        )


def get_production_execution_adapter(
    kind: str = "MOCK",
) -> ProductionExecutionAdapter:
    normalized = (kind or "").strip().upper()
    if normalized != "MOCK":
        raise RuntimeError(
            "Controlled Production Release Executor Step 1 supports MOCK only"
        )
    return MockProductionExecutionAdapter()

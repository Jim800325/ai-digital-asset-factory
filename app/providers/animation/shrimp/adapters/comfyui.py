from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from app.providers.animation.artifact_verification import AdapterArtifactPayload
from app.providers.animation.models import (
    ArtifactProvenance,
    AssetRequirement,
    canonical_json,
)
from app.providers.animation.shrimp.adapters.internal_http import (
    get_bytes,
    post_json,
    validate_internal_base_url,
)


class ComfyUIAssetAdapter:
    adapter_key = "COMFYUI"
    adapter_version = "v0.1"

    def __init__(
        self,
        *,
        base_url: str,
        workflow_path: str,
        generated_license_id: str,
        generated_provenance: str,
        allowed_hosts: list[str] | tuple[str, ...] = (),
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 0.5,
        max_polls: int = 240,
    ):
        self.base_url = validate_internal_base_url(
            base_url,
            allowed_hosts=allowed_hosts,
        )
        self.workflow_path = Path(workflow_path).expanduser().resolve()
        if not self.workflow_path.is_file():
            raise ValueError("ComfyUI workflow_path does not exist")
        if not generated_license_id.strip():
            raise ValueError("generated_license_id is required")
        if not generated_provenance.strip():
            raise ValueError("generated_provenance is required")
        self.generated_license_id = generated_license_id.strip()
        self.generated_provenance = generated_provenance.strip()
        self.timeout_seconds = float(timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.max_polls = int(max_polls)
        if self.max_polls < 1:
            raise ValueError("max_polls must be >= 1")

    def _workflow(
        self,
        *,
        prompt: str,
        seed: int,
        output_prefix: str,
    ) -> tuple[dict, str]:
        raw = self.workflow_path.read_text(encoding="utf-8")
        workflow_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        replacements = {
            "{{PROMPT}}": prompt,
            "{{SEED}}": str(int(seed)),
            "{{OUTPUT_PREFIX}}": output_prefix,
        }
        for key, value in replacements.items():
            raw = raw.replace(key, value)
        workflow = json.loads(raw)
        if not isinstance(workflow, dict):
            raise ValueError("ComfyUI workflow JSON must be an object")
        return workflow, workflow_sha

    def generate(
        self,
        requirement: AssetRequirement,
        *,
        prompt: str,
        seed: int,
        output_prefix: str,
    ) -> AdapterArtifactPayload:
        workflow, workflow_sha = self._workflow(
            prompt=prompt,
            seed=seed,
            output_prefix=output_prefix,
        )
        request_payload = {
            "prompt": workflow,
            "client_id": "shrimp-animation-provider",
        }
        request_sha = hashlib.sha256(
            canonical_json(request_payload).encode("utf-8")
        ).hexdigest()

        body, _ = post_json(
            f"{self.base_url}/prompt",
            request_payload,
            timeout_seconds=self.timeout_seconds,
        )
        response = json.loads(body.decode("utf-8"))
        prompt_id = str(response.get("prompt_id") or "").strip()
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return prompt_id")

        history = None
        for _ in range(self.max_polls):
            time.sleep(self.poll_interval_seconds)
            body, _ = get_bytes(
                f"{self.base_url}/history/{prompt_id}",
                timeout_seconds=self.timeout_seconds,
            )
            payload = json.loads(body.decode("utf-8"))
            history = payload.get(prompt_id)
            if history and history.get("outputs"):
                break
        if not history or not history.get("outputs"):
            raise TimeoutError("ComfyUI generation did not produce an output")

        selected = None
        for node_id in sorted(history["outputs"]):
            output = history["outputs"][node_id]
            images = output.get("images") or []
            if images:
                selected = images[0]
                break
        if not selected:
            raise RuntimeError("ComfyUI history contains no image artifact")

        filename = str(selected.get("filename") or "")
        subfolder = str(selected.get("subfolder") or "")
        output_type = str(selected.get("type") or "output")
        if not filename:
            raise RuntimeError("ComfyUI image output has no filename")

        query = urlencode({
            "filename": filename,
            "subfolder": subfolder,
            "type": output_type,
        })
        storage_uri = f"{self.base_url}/view?{query}"
        content, headers = get_bytes(
            storage_uri,
            timeout_seconds=self.timeout_seconds,
        )
        media_type = (
            headers.get("content-type", "").split(";", 1)[0].strip()
            or (
                "image/png"
                if filename.lower().endswith(".png")
                else "image/jpeg"
            )
        )
        logical_kind = (
            "BACKGROUND"
            if requirement.asset_kind == "BACKGROUND"
            else "CHARACTER"
        )
        return AdapterArtifactPayload(
            logical_key=requirement.requirement_key,
            artifact_kind=logical_kind,
            content=content,
            storage_uri=storage_uri,
            media_type=media_type,
            license_id=self.generated_license_id,
            usage_rights="APPROVED",
            provenance=ArtifactProvenance(
                adapter_key=self.adapter_key,
                adapter_version=self.adapter_version,
                provider_request_id=prompt_id,
                source_reference=storage_uri,
                source_type="COMFYUI_GENERATED",
                provenance=(
                    f"{self.generated_provenance}; "
                    f"workflow_sha256={workflow_sha}"
                ),
                request_sha256=request_sha,
            ),
        )

from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

ALLOWED_EXECUTORS={"OPENHANDS","ACCEPTANCE"}

def resolve_workspace_root() -> Path:
    root=Path(settings.sandbox_workspace_root).expanduser().resolve()
    root.mkdir(parents=True,exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root

def workspace_path_for(workspace_id) -> Path:
    root=resolve_workspace_root()
    path=(root/str(workspace_id)).resolve()
    if path.parent!=root:
        raise RuntimeError("Workspace escaped configured sandbox root")
    return path

def execution_policy_snapshot(*,executor_kind:str,sandbox_image:str)->dict:
    executor=executor_kind.upper().strip()
    openhands=executor=="OPENHANDS"
    return {
        "executor_kind":executor,
        "sandbox_image":sandbox_image,
        "outer_sandbox":"HARDENED_DOCKER",
        "inner_runtime":"process" if openhands else "none",
        "network":"INTERNAL_GATEWAY_ONLY" if openhands else "DENY",
        "docker_network":"internal-gateway" if openhands else "none",
        "workspace":"ISOLATED_RW",
        "filesystem_scope":"WORKSPACE_ONLY",
        "container_rootfs":"READ_ONLY",
        "capabilities":"DROP_ALL",
        "no_new_privileges":True,
        "production_credentials":"DENY",
        "deployment":"DENY",
        "external_side_effects":"DENY",
        "host_docker_socket":"DENY",
        "human_release_required":True,
    }

def _validate_upstream_url(value:str)->None:
    parsed=urlparse(value)
    if parsed.scheme!="https" or not parsed.hostname:
        raise RuntimeError("OpenHands upstream LLM URL must be HTTPS")
    if parsed.username or parsed.password:
        raise RuntimeError("OpenHands upstream LLM URL must not contain credentials")

def validate_execution_policy(*,executor_kind:str,sandbox_image:str)->dict:
    executor=executor_kind.upper().strip()
    if not settings.sandbox_execution_enabled:
        raise RuntimeError("Sandbox execution is disabled")
    if executor not in ALLOWED_EXECUTORS:
        raise RuntimeError("Unsupported sandbox executor")
    if not sandbox_image or any(ch.isspace() for ch in sandbox_image):
        raise RuntimeError("Invalid sandbox image")

    if executor=="OPENHANDS":
        if not settings.openhands_enabled:
            raise RuntimeError("OpenHands sandbox adapter is disabled")
        if settings.openhands_runtime.strip().lower()!="process":
            raise RuntimeError(
                "OpenHands must use process runtime only inside the hardened outer Docker sandbox"
            )
        if not settings.openhands_cli_version.strip():
            raise RuntimeError("OpenHands CLI version is not configured")
        if not settings.openhands_cli_image.strip():
            raise RuntimeError("OpenHands CLI image is not configured")
        if not settings.openhands_model.strip():
            raise RuntimeError("OpenHands model is not configured")
        gateway_mode=settings.openhands_gateway_mode.strip().upper()
        if gateway_mode not in {"MOCK","PROXY"}:
            raise RuntimeError("OpenHands gateway mode must be MOCK or PROXY")
        if gateway_mode=="PROXY":
            _validate_upstream_url(settings.openhands_llm_upstream_url.strip())
            if not settings.openhands_llm_api_key.strip():
                raise RuntimeError("OpenHands upstream LLM API key is not configured")

    return execution_policy_snapshot(
        executor_kind=executor,
        sandbox_image=sandbox_image,
    )

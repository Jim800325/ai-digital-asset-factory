from pathlib import Path

from app.config import settings

ALLOWED_EXECUTORS = {"OPENHANDS","ACCEPTANCE"}

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
    if path.parent != root:
        raise RuntimeError("Workspace escaped configured sandbox root")
    return path

def execution_policy_snapshot(*, executor_kind: str, sandbox_image: str) -> dict:
    executor=executor_kind.upper().strip()
    return {
        "executor_kind":executor,
        "sandbox_image":sandbox_image,
        "network":"DENY",
        "docker_network":"none",
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

def validate_execution_policy(*, executor_kind: str, sandbox_image: str) -> dict:
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
        if settings.openhands_runtime.strip().lower()!="docker":
            raise RuntimeError("OpenHands process/local runtime is forbidden; Docker sandbox is required")
    return execution_policy_snapshot(executor_kind=executor,sandbox_image=sandbox_image)

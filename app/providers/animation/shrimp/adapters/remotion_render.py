from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

from app.providers.animation.shrimp.adapters.remotion import (
    _project_source_sha256,
)


@dataclass(frozen=True)
class RemotionRenderRequest:
    episode_id: str
    composition_record_id: str
    composition_id: str
    animation_manifest_sha256: str
    props_sha256: str
    project_source_sha256: str
    props_uri: str
    expected_width: int
    expected_height: int
    expected_fps: int
    expected_frame_count: int


@dataclass(frozen=True)
class RenderAdapterResult:
    artifact_path: str
    adapter_key: str
    adapter_version: str
    command_sha256: str
    stdout_tail: str
    stderr_tail: str


class VideoRenderAdapter(Protocol):
    adapter_key: str
    adapter_version: str

    def render(self, request: RemotionRenderRequest) -> RenderAdapterResult: ...


def _path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise PermissionError("Controlled Remotion render requires a file:// props URI")
    if parsed.netloc not in ("", "localhost"):
        raise PermissionError("Remote file URI hosts are not allowed")
    return Path(unquote(parsed.path)).resolve()


def _inside(root: Path, path: Path) -> bool:
    return root == path or root in path.parents


class ControlledRemotionRenderAdapter:
    adapter_key = "REMOTION_CONTROLLED"
    adapter_version = "v0.1"

    def __init__(
        self,
        *,
        project_dir: str,
        props_allowed_root: str,
        output_root: str,
        entrypoint: str = "src/index.tsx",
        remotion_cli: str = "remotion",
        timeout_seconds: float = 300.0,
    ):
        if not project_dir.strip():
            raise ValueError("project_dir is required")
        if not props_allowed_root.strip():
            raise ValueError("props_allowed_root is required")
        if not output_root.strip():
            raise ValueError("output_root is required")
        if not remotion_cli.strip():
            raise ValueError("remotion_cli is required")

        self.project_dir = Path(project_dir).expanduser().resolve()
        self.props_allowed_root = Path(props_allowed_root).expanduser().resolve()
        self.output_root = Path(output_root).expanduser().resolve()
        self.entrypoint = (self.project_dir / entrypoint).resolve()
        self.remotion_cli = remotion_cli.strip()
        self.timeout_seconds = float(timeout_seconds)

        if not self.project_dir.is_dir():
            raise ValueError("Remotion project_dir does not exist")
        if not self.entrypoint.is_file() or not _inside(self.project_dir, self.entrypoint):
            raise ValueError("Remotion entrypoint is invalid")
        self.props_allowed_root.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def render(self, request: RemotionRenderRequest) -> RenderAdapterResult:
        props_path = _path_from_file_uri(request.props_uri)
        if not _inside(self.props_allowed_root, props_path):
            raise PermissionError("Remotion props path escapes allowed root")
        if not props_path.is_file():
            raise FileNotFoundError("Frozen Remotion props file is missing")

        props_bytes = props_path.read_bytes()
        props_sha = hashlib.sha256(props_bytes).hexdigest()
        if props_sha != request.props_sha256:
            raise ValueError("Frozen Remotion props SHA-256 mismatch")

        source_sha = _project_source_sha256(self.project_dir)
        if source_sha != request.project_source_sha256:
            raise ValueError("Frozen Remotion project source SHA-256 mismatch")

        safe_episode = "".join(
            ch for ch in request.episode_id if ch.isalnum() or ch in ("-", "_")
        )[:64]
        if not safe_episode:
            raise ValueError("episode_id cannot produce a safe output name")

        output_path = (
            self.output_root
            / f"{safe_episode}-{request.animation_manifest_sha256[:16]}.mp4"
        ).resolve()
        if not _inside(self.output_root, output_path):
            raise PermissionError("Render output path escapes output root")
        if output_path.exists():
            output_path.unlink()

        command = [
            self.remotion_cli,
            "render",
            str(self.entrypoint),
            request.composition_id,
            str(output_path),
            f"--props={props_path}",
            "--codec=h264",
        ]
        command_sha = hashlib.sha256(
            ("\0".join(command)).encode("utf-8")
        ).hexdigest()

        env = os.environ.copy()
        env["CI"] = env.get("CI", "1")
        completed = subprocess.run(
            command,
            cwd=str(self.project_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=self.timeout_seconds,
            check=False,
        )
        stdout_tail = (completed.stdout or "")[-8000:]
        stderr_tail = (completed.stderr or "")[-8000:]
        if completed.returncode != 0:
            raise RuntimeError(
                "Controlled Remotion render failed "
                f"(exit={completed.returncode}): {stderr_tail[-2000:]}"
            )
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError("Remotion exited successfully without an MP4 artifact")

        return RenderAdapterResult(
            artifact_path=str(output_path),
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
            command_sha256=command_sha,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
        )

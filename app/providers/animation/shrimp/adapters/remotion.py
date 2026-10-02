from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from app.providers.animation.models import (
    AnimationTimelineManifest,
    RemotionCompositionPayload,
    canonical_json,
)


class AnimationCompositionAdapter(Protocol):
    adapter_key: str
    adapter_version: str

    def prepare(
        self,
        timeline: AnimationTimelineManifest,
    ) -> RemotionCompositionPayload: ...


def _project_source_sha256(project_dir: Path) -> str:
    digest = hashlib.sha256()
    excluded = {"node_modules", ".git", "out", "dist", ".next"}
    files = [
        path
        for path in project_dir.rglob("*")
        if path.is_file()
        and not any(part in excluded for part in path.parts)
    ]
    for path in sorted(files, key=lambda item: item.as_posix()):
        relative = path.relative_to(project_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if not files:
        raise ValueError("Remotion project contains no source files")
    return digest.hexdigest()


class RemotionRendererAdapter:
    adapter_key = "REMOTION"
    adapter_version = "v0.1"

    def __init__(
        self,
        *,
        project_dir: str,
        props_output_root: str,
        entrypoint: str = "src/index.tsx",
        composition_id: str = "ShrimpAnimation",
        remotion_cli: str = "remotion",
    ):
        if not project_dir.strip():
            raise ValueError("Remotion project_dir is required")
        if not props_output_root.strip():
            raise ValueError("Remotion props_output_root is required")
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.props_output_root = Path(
            props_output_root
        ).expanduser().resolve()
        if not self.project_dir.is_dir():
            raise ValueError("Remotion project_dir does not exist")
        self.entrypoint = (self.project_dir / entrypoint).resolve()
        if (
            self.project_dir != self.entrypoint
            and self.project_dir not in self.entrypoint.parents
        ):
            raise PermissionError("Remotion entrypoint escapes project_dir")
        if not self.entrypoint.is_file():
            raise ValueError("Remotion entrypoint does not exist")
        if not composition_id.strip():
            raise ValueError("composition_id is required")
        if not remotion_cli.strip():
            raise ValueError("remotion_cli is required")
        self.composition_id = composition_id.strip()
        self.remotion_cli = remotion_cli.strip()

    def prepare(
        self,
        timeline: AnimationTimelineManifest,
    ) -> RemotionCompositionPayload:
        props = {
            "animation": timeline.model_dump(mode="json"),
        }
        encoded = canonical_json(props).encode("utf-8")
        props_sha = hashlib.sha256(encoded).hexdigest()
        output_dir = self.props_output_root / timeline.episode_id
        output_dir.mkdir(parents=True, exist_ok=True)
        props_path = (output_dir / f"{props_sha}.json").resolve()
        if (
            self.props_output_root != props_path
            and self.props_output_root not in props_path.parents
        ):
            raise PermissionError("Remotion props path escapes output root")
        props_path.write_bytes(encoded)
        reread = props_path.read_bytes()
        if hashlib.sha256(reread).hexdigest() != props_sha:
            raise RuntimeError("Remotion props verification failed after write")

        return RemotionCompositionPayload(
            composition_id=self.composition_id,
            props_uri=props_path.as_uri(),
            props_sha256=props_sha,
            project_source_sha256=_project_source_sha256(
                self.project_dir
            ),
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
            props=props,
        )

    def build_render_command(
        self,
        *,
        props_path: str,
        output_path: str,
    ) -> list[str]:
        return [
            self.remotion_cli,
            "render",
            str(self.entrypoint),
            self.composition_id,
            str(Path(output_path)),
            f"--props={Path(props_path)}",
        ]

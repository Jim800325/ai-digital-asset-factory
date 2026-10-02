from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import text

from app.db import engine


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ASSET_KINDS = {
    "CHARACTER_BASE",
    "CHARACTER_VARIANT",
    "BACKGROUND",
    "PROP",
}
RIGHTS = {"APPROVED", "REVIEW_REQUIRED", "BLOCKED"}
VOICE_SOURCE_TYPES = {
    "SYNTHETIC",
    "SELF_RECORDED",
    "LICENSED",
    "CLONED_WITH_CONSENT",
    "UNKNOWN",
}

BUILTIN_ACTIONS = (
    ("idle", "idle", {}),
    ("talk", "talk", {}),
    ("walk_left", "walk_left", {}),
    ("walk_right", "walk_right", {}),
    ("enter", "enter", {"direction": "string"}),
    ("exit", "exit", {"direction": "string"}),
    ("jump", "jump", {}),
    ("shake", "shake", {}),
    ("nod", "nod", {}),
    ("bow", "bow", {}),
    ("turn", "turn", {}),
    ("scale_pulse", "scale_pulse", {}),
    ("hit_reaction", "hit_reaction", {}),
    ("surprised", "surprised", {}),
    ("angry", "angry", {}),
    ("laugh", "laugh", {}),
)
BUILTIN_CAMERAS = (
    ("static", "static", {}),
    ("pan", "pan", {"from": "number", "to": "number"}),
    ("zoom", "zoom", {"from_scale": "number", "to_scale": "number"}),
    ("push_in", "push_in", {"from_scale": "number", "to_scale": "number"}),
    ("pull_out", "pull_out", {"from_scale": "number", "to_scale": "number"}),
    ("shake", "shake", {}),
    ("focus_left", "focus_left", {}),
    ("focus_right", "focus_right", {}),
)


def ensure_builtin_animation_primitives() -> dict:
    with engine.begin() as db:
        for key, primitive, schema in BUILTIN_ACTIONS:
            db.execute(
                text("""
                  INSERT INTO animation_action_registry(
                    action_key,renderer_primitive,parameter_schema,
                    deterministic,active,updated_at)
                  VALUES(
                    :key,:primitive,CAST(:schema AS jsonb),true,true,now())
                  ON CONFLICT(action_key) DO UPDATE SET
                    renderer_primitive=excluded.renderer_primitive,
                    parameter_schema=excluded.parameter_schema,
                    deterministic=true,
                    active=true,
                    updated_at=now()
                """),
                {
                    "key": key,
                    "primitive": primitive,
                    "schema": _canonical_json(schema),
                },
            )
        for key, primitive, schema in BUILTIN_CAMERAS:
            db.execute(
                text("""
                  INSERT INTO animation_camera_registry(
                    camera_key,renderer_primitive,parameter_schema,
                    deterministic,active,updated_at)
                  VALUES(
                    :key,:primitive,CAST(:schema AS jsonb),true,true,now())
                  ON CONFLICT(camera_key) DO UPDATE SET
                    renderer_primitive=excluded.renderer_primitive,
                    parameter_schema=excluded.parameter_schema,
                    deterministic=true,
                    active=true,
                    updated_at=now()
                """),
                {
                    "key": key,
                    "primitive": primitive,
                    "schema": _canonical_json(schema),
                },
            )
    return {
        "actions": len(BUILTIN_ACTIONS),
        "cameras": len(BUILTIN_CAMERAS),
    }



def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _snapshot_sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def register_reusable_asset(
    *,
    asset_key: str,
    asset_kind: str,
    storage_uri: str,
    sha256: str,
    license_id: str,
    provenance: str,
    usage_rights: str = "REVIEW_REQUIRED",
    media_type: str = "image/png",
    metadata: dict | None = None,
) -> dict:
    kind = asset_kind.upper().strip()
    rights = usage_rights.upper().strip()
    if kind not in ASSET_KINDS:
        raise ValueError("unsupported asset_kind")
    if rights not in RIGHTS:
        raise ValueError("unsupported usage_rights")
    if not SHA256_RE.fullmatch(sha256):
        raise ValueError("sha256 must contain 64 lowercase hex characters")
    if not asset_key.strip() or not storage_uri.strip():
        raise ValueError("asset_key and storage_uri are required")
    if not license_id.strip() or not provenance.strip():
        raise ValueError("license_id and provenance are required")

    with engine.begin() as db:
        row = db.execute(
            text("""
              INSERT INTO animation_reusable_assets(
                asset_key,asset_kind,storage_uri,media_type,sha256,
                license_id,provenance,usage_rights,metadata,active,updated_at)
              VALUES(
                :asset_key,:asset_kind,:storage_uri,:media_type,:sha256,
                :license_id,:provenance,:usage_rights,
                CAST(:metadata AS jsonb),true,now())
              ON CONFLICT(asset_key) DO UPDATE SET
                asset_kind=excluded.asset_kind,
                storage_uri=excluded.storage_uri,
                media_type=excluded.media_type,
                sha256=excluded.sha256,
                license_id=excluded.license_id,
                provenance=excluded.provenance,
                usage_rights=excluded.usage_rights,
                metadata=excluded.metadata,
                active=true,
                updated_at=now()
              RETURNING id,asset_key,asset_kind,storage_uri,media_type,sha256,
                        license_id,provenance,usage_rights,active
            """),
            {
                "asset_key": asset_key.strip(),
                "asset_kind": kind,
                "storage_uri": storage_uri.strip(),
                "media_type": media_type.strip() or "application/octet-stream",
                "sha256": sha256,
                "license_id": license_id.strip(),
                "provenance": provenance.strip(),
                "usage_rights": rights,
                "metadata": _canonical_json(metadata or {}),
            },
        ).mappings().one()
    return dict(row)


def _asset_id(
    db,
    asset_key: str | None,
    *,
    expected_kind: str | None = None,
):
    if not asset_key:
        return None
    row = db.execute(
        text("""
          SELECT id,asset_kind
          FROM animation_reusable_assets
          WHERE asset_key=:asset_key AND active=true
        """),
        {"asset_key": asset_key},
    ).mappings().one_or_none()
    if row is None:
        raise LookupError(f"Reusable asset not found: {asset_key}")
    if expected_kind and row["asset_kind"] != expected_kind:
        raise ValueError(
            f"Asset {asset_key} is {row['asset_kind']}, expected {expected_kind}"
        )
    return row["id"]


def register_voice_profile(
    *,
    voice_profile_id: str,
    provenance: str,
    usage_rights: str = "REVIEW_REQUIRED",
    adapter_hint: str = "UNBOUND",
    language: str = "zh-CN",
    source_type: str = "UNKNOWN",
    metadata: dict | None = None,
) -> dict:
    rights = usage_rights.upper().strip()
    source = source_type.upper().strip()
    if rights not in RIGHTS:
        raise ValueError("unsupported usage_rights")
    if source not in VOICE_SOURCE_TYPES:
        raise ValueError("unsupported voice source_type")
    if not voice_profile_id.strip() or not provenance.strip():
        raise ValueError("voice_profile_id and provenance are required")

    with engine.begin() as db:
        row = db.execute(
            text("""
              INSERT INTO animation_voice_profiles(
                voice_profile_id,adapter_hint,language,source_type,
                provenance,usage_rights,metadata,active,updated_at)
              VALUES(
                :voice_profile_id,:adapter_hint,:language,:source_type,
                :provenance,:usage_rights,CAST(:metadata AS jsonb),true,now())
              ON CONFLICT(voice_profile_id) DO UPDATE SET
                adapter_hint=excluded.adapter_hint,
                language=excluded.language,
                source_type=excluded.source_type,
                provenance=excluded.provenance,
                usage_rights=excluded.usage_rights,
                metadata=excluded.metadata,
                active=true,
                updated_at=now()
              RETURNING voice_profile_id,adapter_hint,language,source_type,
                        provenance,usage_rights,active
            """),
            {
                "voice_profile_id": voice_profile_id.strip(),
                "adapter_hint": adapter_hint.strip() or "UNBOUND",
                "language": language.strip() or "zh-CN",
                "source_type": source,
                "provenance": provenance.strip(),
                "usage_rights": rights,
                "metadata": _canonical_json(metadata or {}),
            },
        ).mappings().one()
    return dict(row)


def register_character(
    *,
    character_id: str,
    display_name: str,
    base_asset_key: str | None = None,
    default_scale: float = 1.0,
    anchor_points: dict | None = None,
    voice_profile_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    with engine.begin() as db:
        base_asset_id = _asset_id(
            db,
            base_asset_key,
            expected_kind="CHARACTER_BASE",
        )
        if voice_profile_id:
            exists = db.execute(
                text("""
                  SELECT 1
                  FROM animation_voice_profiles
                  WHERE voice_profile_id=:id AND active=true
                """),
                {"id": voice_profile_id},
            ).scalar_one_or_none()
            if exists is None:
                raise LookupError(f"Voice profile not found: {voice_profile_id}")
        row = db.execute(
            text("""
              INSERT INTO animation_character_registry(
                character_id,display_name,base_asset_id,default_scale,
                anchor_points,voice_profile_id,metadata,active,updated_at)
              VALUES(
                :character_id,:display_name,:base_asset_id,:default_scale,
                CAST(:anchor_points AS jsonb),:voice_profile_id,
                CAST(:metadata AS jsonb),true,now())
              ON CONFLICT(character_id) DO UPDATE SET
                display_name=excluded.display_name,
                base_asset_id=excluded.base_asset_id,
                default_scale=excluded.default_scale,
                anchor_points=excluded.anchor_points,
                voice_profile_id=excluded.voice_profile_id,
                metadata=excluded.metadata,
                active=true,
                updated_at=now()
              RETURNING character_id,display_name,base_asset_id,
                        default_scale,anchor_points,voice_profile_id,active
            """),
            {
                "character_id": character_id.strip(),
                "display_name": display_name.strip(),
                "base_asset_id": base_asset_id,
                "default_scale": float(default_scale),
                "anchor_points": _canonical_json(anchor_points or {}),
                "voice_profile_id": voice_profile_id,
                "metadata": _canonical_json(metadata or {}),
            },
        ).mappings().one()
    return dict(row)


def register_character_variant(
    *,
    character_id: str,
    variant_name: str,
    asset_key: str,
) -> dict:
    with engine.begin() as db:
        asset_id = _asset_id(
            db,
            asset_key,
            expected_kind="CHARACTER_VARIANT",
        )
        character = db.execute(
            text("""
              SELECT 1 FROM animation_character_registry
              WHERE character_id=:id AND active=true
            """),
            {"id": character_id},
        ).scalar_one_or_none()
        if character is None:
            raise LookupError(f"Character not found: {character_id}")
        row = db.execute(
            text("""
              INSERT INTO animation_character_variants(
                character_id,variant_name,asset_id,active,updated_at)
              VALUES(:character_id,:variant_name,:asset_id,true,now())
              ON CONFLICT(character_id,variant_name) DO UPDATE SET
                asset_id=excluded.asset_id,
                active=true,
                updated_at=now()
              RETURNING character_id,variant_name,asset_id,active
            """),
            {
                "character_id": character_id,
                "variant_name": variant_name.strip(),
                "asset_id": asset_id,
            },
        ).mappings().one()
    return dict(row)


def register_background(
    *,
    background_id: str,
    asset_key: str | None = None,
    metadata: dict | None = None,
) -> dict:
    with engine.begin() as db:
        asset_id = _asset_id(
            db,
            asset_key,
            expected_kind="BACKGROUND",
        )
        row = db.execute(
            text("""
              INSERT INTO animation_background_registry(
                background_id,asset_id,metadata,active,updated_at)
              VALUES(
                :background_id,:asset_id,CAST(:metadata AS jsonb),true,now())
              ON CONFLICT(background_id) DO UPDATE SET
                asset_id=excluded.asset_id,
                metadata=excluded.metadata,
                active=true,
                updated_at=now()
              RETURNING background_id,asset_id,metadata,active
            """),
            {
                "background_id": background_id.strip(),
                "asset_id": asset_id,
                "metadata": _canonical_json(metadata or {}),
            },
        ).mappings().one()
    return dict(row)


def _asset_row_dict(row) -> dict | None:
    if row is None or row.get("asset_key") is None:
        return None
    return {
        "asset_key": row["asset_key"],
        "asset_kind": row["asset_kind"],
        "storage_uri": row["storage_uri"],
        "media_type": row["media_type"],
        "sha256": row["sha256"],
        "license_id": row["license_id"],
        "provenance": row["provenance"],
        "usage_rights": row["usage_rights"],
    }


def load_asset_registry_snapshot(
    *,
    character_ids: list[str],
    background_ids: list[str],
    action_keys: list[str],
    camera_keys: list[str],
) -> dict:
    ensure_builtin_animation_primitives()
    with engine.connect() as db:
        characters = []
        for character_id in sorted(set(character_ids)):
            row = db.execute(
                text("""
                  SELECT c.character_id,c.display_name,c.default_scale,
                         c.anchor_points,c.voice_profile_id,
                         a.asset_key,a.asset_kind,a.storage_uri,a.media_type,
                         a.sha256,a.license_id,a.provenance,a.usage_rights
                  FROM animation_character_registry c
                  LEFT JOIN animation_reusable_assets a
                    ON a.id=c.base_asset_id AND a.active=true
                  WHERE c.character_id=:id AND c.active=true
                """),
                {"id": character_id},
            ).mappings().one_or_none()
            variants = [
                {
                    "variant_name": item["variant_name"],
                    "asset": _asset_row_dict(item),
                }
                for item in db.execute(
                    text("""
                      SELECT v.variant_name,
                             a.asset_key,a.asset_kind,a.storage_uri,a.media_type,
                             a.sha256,a.license_id,a.provenance,a.usage_rights
                      FROM animation_character_variants v
                      JOIN animation_reusable_assets a
                        ON a.id=v.asset_id AND a.active=true
                      WHERE v.character_id=:id AND v.active=true
                      ORDER BY v.variant_name
                    """),
                    {"id": character_id},
                ).mappings().all()
            ]
            if row is None:
                characters.append({
                    "character_id": character_id,
                    "registered": False,
                    "base_asset": None,
                    "variants": variants,
                })
            else:
                characters.append({
                    "character_id": row["character_id"],
                    "display_name": row["display_name"],
                    "default_scale": float(row["default_scale"]),
                    "anchor_points": row["anchor_points"],
                    "voice_profile_id": row["voice_profile_id"],
                    "registered": True,
                    "base_asset": _asset_row_dict(row),
                    "variants": variants,
                })

        backgrounds = []
        for background_id in sorted(set(background_ids)):
            row = db.execute(
                text("""
                  SELECT b.background_id,
                         a.asset_key,a.asset_kind,a.storage_uri,a.media_type,
                         a.sha256,a.license_id,a.provenance,a.usage_rights
                  FROM animation_background_registry b
                  LEFT JOIN animation_reusable_assets a
                    ON a.id=b.asset_id AND a.active=true
                  WHERE b.background_id=:id AND b.active=true
                """),
                {"id": background_id},
            ).mappings().one_or_none()
            backgrounds.append({
                "background_id": background_id,
                "registered": row is not None,
                "asset": _asset_row_dict(row),
            })

        actions = [
            dict(row)
            for row in db.execute(
                text("""
                  SELECT action_key,renderer_primitive,parameter_schema,
                         deterministic,active
                  FROM animation_action_registry
                  WHERE action_key=ANY(:keys) AND active=true
                  ORDER BY action_key
                """),
                {"keys": sorted(set(action_keys)) or ["__none__"]},
            ).mappings().all()
        ]
        cameras = [
            dict(row)
            for row in db.execute(
                text("""
                  SELECT camera_key,renderer_primitive,parameter_schema,
                         deterministic,active
                  FROM animation_camera_registry
                  WHERE camera_key=ANY(:keys) AND active=true
                  ORDER BY camera_key
                """),
                {"keys": sorted(set(camera_keys)) or ["__none__"]},
            ).mappings().all()
        ]

    payload = {
        "characters": characters,
        "backgrounds": backgrounds,
        "actions": actions,
        "cameras": cameras,
    }
    return {
        **payload,
        "snapshot_sha256": _snapshot_sha(payload),
    }


def load_voice_registry_snapshot(
    *,
    character_ids: list[str],
    requested_profile_ids: list[str],
) -> dict:
    with engine.connect() as db:
        characters = [
            dict(row)
            for row in db.execute(
                text("""
                  SELECT character_id,voice_profile_id
                  FROM animation_character_registry
                  WHERE character_id=ANY(:ids) AND active=true
                  ORDER BY character_id
                """),
                {"ids": sorted(set(character_ids)) or ["__none__"]},
            ).mappings().all()
        ]
        profile_ids = set(requested_profile_ids)
        profile_ids.update(
            row["voice_profile_id"]
            for row in characters
            if row.get("voice_profile_id")
        )
        profiles = [
            dict(row)
            for row in db.execute(
                text("""
                  SELECT voice_profile_id,adapter_hint,language,source_type,
                         provenance,usage_rights,metadata,active
                  FROM animation_voice_profiles
                  WHERE voice_profile_id=ANY(:ids) AND active=true
                  ORDER BY voice_profile_id
                """),
                {"ids": sorted(profile_ids) or ["__none__"]},
            ).mappings().all()
        ]
    payload = {
        "characters": characters,
        "voice_profiles": profiles,
    }
    return {
        **payload,
        "snapshot_sha256": _snapshot_sha(payload),
    }


def list_animation_registry() -> dict:
    ensure_builtin_animation_primitives()
    with engine.connect() as db:
        assets = [
            dict(row)
            for row in db.execute(text("""
              SELECT asset_key,asset_kind,storage_uri,media_type,sha256,
                     license_id,provenance,usage_rights,active
              FROM animation_reusable_assets
              WHERE active=true
              ORDER BY asset_kind,asset_key
            """)).mappings().all()
        ]
        characters = [
            dict(row)
            for row in db.execute(text("""
              SELECT character_id,display_name,default_scale,anchor_points,
                     voice_profile_id,active
              FROM animation_character_registry
              WHERE active=true
              ORDER BY character_id
            """)).mappings().all()
        ]
        backgrounds = [
            dict(row)
            for row in db.execute(text("""
              SELECT background_id,asset_id,metadata,active
              FROM animation_background_registry
              WHERE active=true
              ORDER BY background_id
            """)).mappings().all()
        ]
        actions = [
            dict(row)
            for row in db.execute(text("""
              SELECT action_key,renderer_primitive,parameter_schema,
                     deterministic,active
              FROM animation_action_registry
              WHERE active=true
              ORDER BY action_key
            """)).mappings().all()
        ]
        cameras = [
            dict(row)
            for row in db.execute(text("""
              SELECT camera_key,renderer_primitive,parameter_schema,
                     deterministic,active
              FROM animation_camera_registry
              WHERE active=true
              ORDER BY camera_key
            """)).mappings().all()
        ]
        voices = [
            dict(row)
            for row in db.execute(text("""
              SELECT voice_profile_id,adapter_hint,language,source_type,
                     provenance,usage_rights,active
              FROM animation_voice_profiles
              WHERE active=true
              ORDER BY voice_profile_id
            """)).mappings().all()
        ]
    return {
        "assets": assets,
        "characters": characters,
        "backgrounds": backgrounds,
        "actions": actions,
        "cameras": cameras,
        "voice_profiles": voices,
    }

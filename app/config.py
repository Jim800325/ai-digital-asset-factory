from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://assetfactory:assetfactory@postgres:5432/assetfactory"
    preview_database_url: str = ""
    preview_acceptance_key: str = ""
    deployment_authorization_preview_only: bool = True
    redis_url: str = "redis://redis:6379/0"
    database_connect_timeout_seconds: int = 5
    database_read_retry_attempts: int = 2
    database_application_name: str = "ai-digital-asset-factory"
    user_agent: str = "AI-Digital-Asset-Factory/0.3"
    request_timeout_seconds: int = 20
    max_pages_per_run: int = 20
    seed_urls: str = "https://news.ycombinator.com/,https://github.com/trending"
    github_token: str = ""
    github_discovery_enabled: bool = True
    github_results_per_query: int = 10
    side_business_registry_enabled: bool = True
    side_business_discovery_enabled: bool = True
    side_business_results_per_query: int = 8
    side_business_max_candidates_per_run: int = 40
    side_business_build_ready_score: float = 75.0
    side_business_composition_enabled: bool = True
    side_business_composition_candidates_per_slot: int = 3
    side_business_composition_max_per_run: int = 24
    side_business_composition_ready_score: float = 72.0
    side_business_composition_emit_limit: int = 12
    production_provider_contract_enabled: bool = True
    shrimp_animation_provider_enabled: bool = True
    shrimp_asset_adapter: str = "DISABLED"
    shrimp_voice_adapter: str = "DISABLED"
    shrimp_comfyui_base_url: str = ""
    shrimp_comfyui_workflow_path: str = ""
    shrimp_gptsovits_base_url: str = ""
    shrimp_gptsovits_tts_path: str = "/tts"
    shrimp_internal_adapter_allowed_hosts: str = ""
    shrimp_artifact_allowed_roots: str = ""
    shrimp_generated_asset_license_id: str = ""
    shrimp_generated_asset_provenance: str = ""
    shrimp_generated_voice_license_id: str = ""
    shrimp_generated_voice_provenance: str = ""
    shrimp_adapter_timeout_seconds: float = 120.0
    shrimp_animation_adapter: str = "DISABLED"
    shrimp_remotion_project_dir: str = "renderer/remotion"
    shrimp_remotion_entrypoint: str = "src/index.tsx"
    shrimp_remotion_composition_id: str = "ShrimpAnimation"
    shrimp_remotion_props_output_root: str = ""
    shrimp_remotion_cli: str = "remotion"
    shrimp_render_adapter: str = "DISABLED"
    shrimp_remotion_render_output_root: str = ""
    shrimp_remotion_render_timeout_seconds: float = 300.0
    shrimp_ffprobe_cli: str = "ffprobe"
    shrimp_qc_analyzer: str = "DISABLED"
    shrimp_qc_ffmpeg_cli: str = "ffmpeg"
    shrimp_qc_max_black_segment_ms: int = 1500
    shrimp_qc_max_black_ratio: float = 0.10
    shrimp_qc_max_freeze_segment_ms: int = 8000
    shrimp_qc_max_freeze_ratio: float = 0.50
    shrimp_qc_max_dialogue_silence_ratio: float = 0.80
    shrimp_qc_analysis_timeout_seconds: float = 180.0
    shrimp_package_enabled: bool = False
    shrimp_package_output_root: str = ""
    shrimp_human_review_key: str = ""
    shrimp_publish_authorization_key: str = ""
    shrimp_publish_execution_key: str = ""
    shrimp_publish_executor_enabled: bool = False
    shrimp_publish_execution_adapter: str = "MOCK"
    shrimp_publish_execution_allowed_account_refs: str = ""
    shrimp_publish_execution_denied_account_refs: str = ""
    shrimp_publish_execution_allowed_target_keys: str = ""
    shrimp_publish_execution_denied_target_keys: str = ""
    shrimp_bilibili_live_acceptance_enabled: bool = False
    shrimp_bilibili_live_acceptance_key: str = ""
    shrimp_bilibili_sessdata: str = ""
    shrimp_bilibili_bili_jct: str = ""
    shrimp_bilibili_dede_user_id: str = ""
    shrimp_bilibili_dede_user_id_ckmd5: str = ""
    shrimp_bilibili_default_tid: int = 122
    shrimp_bilibili_live_acceptance_max_media_bytes: int = 52428800
    shrimp_bilibili_health_max_age_minutes: int = 360
    shrimp_bilibili_preflight_recheck_max_age_minutes: int = 5
    shrimp_bilibili_health_failure_threshold: int = 3
    shrimp_bilibili_health_monitor_key: str = ""
    shrimp_bilibili_reservation_ttl_minutes: int = 15
    shrimp_bilibili_stuck_claim_minutes: int = 30
    shrimp_bilibili_claim_warning_minutes: int = 45
    shrimp_bilibili_claim_critical_minutes: int = 120
    shrimp_bilibili_circuit_ambiguity_threshold: int = 3
    shrimp_bilibili_circuit_provider_failure_threshold: int = 3
    shrimp_bilibili_circuit_cooldown_minutes: int = 60
    shrimp_bilibili_recovery_approval_key: str = ""
    shrimp_bilibili_notification_webhook_url: str = ""
    shrimp_bilibili_notification_webhook_token: str = ""
    shrimp_bilibili_incident_ack_sla_minutes: int = 15
    shrimp_bilibili_incident_recovery_sla_minutes: int = 120
    shrimp_bilibili_incident_default_owner: str = "publisher-oncall"
    shrimp_bilibili_incident_secondary_owner: str = "publisher-backup"
    shrimp_bilibili_incident_ops_key: str = ""
    shrimp_bilibili_reliability_window_days: int = 30
    shrimp_bilibili_ack_slo_target_percent: float = 95.0
    shrimp_bilibili_recovery_slo_target_percent: float = 95.0
    shrimp_bilibili_ambiguity_target_percent: float = 5.0
    shrimp_bilibili_recurrence_threshold: int = 2
    shrimp_bilibili_burn_short_window_hours: int = 6
    shrimp_bilibili_burn_long_window_hours: int = 24
    shrimp_bilibili_burn_watch_threshold: float = 1.0
    shrimp_bilibili_burn_fast_threshold: float = 2.0
    shrimp_bilibili_regression_score_drop_points: float = 8.0
    shrimp_bilibili_regression_rate_drop_percent: float = 10.0
    shrimp_bilibili_regression_ambiguity_increase_percent: float = 5.0
    shrimp_bilibili_reliability_governance_key: str = ""
    shrimp_bilibili_reliability_policy_apply_key: str = ""
    shrimp_bilibili_reliability_restore_approval_key: str = ""
    shrimp_bilibili_reliability_restore_apply_key: str = ""
    shrimp_bilibili_observation_stage_minutes: int = 360
    shrimp_bilibili_observation_min_executions: int = 1
    shrimp_bilibili_restore_acceptance_key: str = ""
    shrimp_bilibili_certification_valid_days: int = 30
    shrimp_bilibili_certification_expiry_warning_days: int = 7
    shrimp_bilibili_recertification_key: str = ""
    shrimp_bilibili_renewal_sla_hours: int = 72
    shrimp_bilibili_missed_renewal_critical_hours: int = 24
    shrimp_bilibili_audit_signing_private_key_pem_b64: str = ""
    shrimp_bilibili_audit_signing_provider: str = "LOCAL_PEM"
    shrimp_bilibili_openbao_url: str = ""
    shrimp_bilibili_openbao_token: str = ""
    shrimp_bilibili_openbao_transit_mount: str = "transit"
    shrimp_bilibili_openbao_key_name: str = "shrimp-bilibili-audit"
    shrimp_bilibili_openbao_timeout_seconds: float = 10.0
    shrimp_bilibili_openbao_rotation_enabled: bool = False
    shrimp_bilibili_tuf_root_valid_days: int = 365
    shrimp_bilibili_tuf_root_threshold: int = 1
    shrimp_bilibili_signing_key_rotation_key: str = ""
    shrimp_bilibili_root_transition_approver_a_key: str = ""
    shrimp_bilibili_root_transition_approver_b_key: str = ""
    shrimp_bilibili_openbao_live_acceptance_enabled: bool = False
    shrimp_bilibili_hsm_provider: str = "DISABLED"
    shrimp_bilibili_pkcs11_module: str = ""
    shrimp_bilibili_pkcs11_token_label: str = ""
    shrimp_bilibili_pkcs11_user_pin: str = ""
    shrimp_bilibili_pkcs11_key_label: str = "shrimp-bilibili-root"
    shrimp_bilibili_pkcs11_key_id_hex: str = "10b25"
    shrimp_bilibili_hsm_live_acceptance_enabled: bool = False
    shrimp_bilibili_root_ceremony_key: str = ""
    shrimp_bilibili_rekor_url: str = ""
    shrimp_bilibili_rekor_enabled: bool = False
    shrimp_bilibili_rekor_timeout_seconds: float = 10.0
    shrimp_bilibili_tsa_url: str = ""
    shrimp_bilibili_tsa_enabled: bool = False
    shrimp_bilibili_tsa_timeout_seconds: float = 10.0
    cron_secret: str = ""
    human_approval_key: str = ""
    human_release_key: str = ""
    human_deployment_key: str = ""
    human_production_execution_key: str = ""
    controlled_production_executor_enabled: bool = False
    production_promotion_enabled: bool = False
    production_rollback_enabled: bool = False
    production_execution_adapter: str = "MOCK"
    production_execution_preview_only: bool = True
    production_execution_allowed_project_ids: str = ""
    production_execution_allowed_team_ids: str = ""
    production_execution_denied_project_ids: str = "prj_orLCRCIm7aVfImH8ihB3gponFOEl"
    vercel_controlled_executor_token: str = ""
    vercel_controlled_executor_api_base: str = "https://api.vercel.com"
    vercel_controlled_executor_timeout_seconds: int = 30
    sandbox_execution_enabled: bool = False
    sandbox_workspace_root: str = "/tmp/asset-factory-workspaces"
    sandbox_image: str = "python:3.12-slim"
    sandbox_timeout_seconds: int = 300
    openhands_enabled: bool = False
    openhands_runtime: str = "process"
    openhands_cli_version: str = "1.16.0"
    openhands_cli_image: str = "asset-factory-openhands:1.16.0"
    openhands_gateway_mode: str = "PROXY"
    openhands_gateway_image: str = "python:3.12-slim"
    openhands_model: str = ""
    openhands_llm_upstream_url: str = ""
    openhands_llm_api_key: str = ""
    openhands_allowed_models: str = ""
    openhands_max_requests: int = 8
    openhands_max_prompt_tokens_per_request: int = 12000
    openhands_max_completion_tokens_per_request: int = 4000
    openhands_max_total_tokens: int = 24000
    openhands_max_cost_per_request_usd: float = 0.10
    openhands_max_cost_usd: float = 0.25
    openhands_input_cost_per_1m_usd: float = 0.0
    openhands_output_cost_per_1m_usd: float = 0.0
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
    )

    @property
    def shrimp_publish_execution_allowed_account_ref_list(self) -> list[str]:
        return [
            x.strip()
            for x in self.shrimp_publish_execution_allowed_account_refs.split(",")
            if x.strip()
        ]

    @property
    def shrimp_publish_execution_denied_account_ref_list(self) -> list[str]:
        return [
            x.strip()
            for x in self.shrimp_publish_execution_denied_account_refs.split(",")
            if x.strip()
        ]

    @property
    def shrimp_publish_execution_allowed_target_key_list(self) -> list[str]:
        return [
            x.strip()
            for x in self.shrimp_publish_execution_allowed_target_keys.split(",")
            if x.strip()
        ]

    @property
    def shrimp_publish_execution_denied_target_key_list(self) -> list[str]:
        return [
            x.strip()
            for x in self.shrimp_publish_execution_denied_target_keys.split(",")
            if x.strip()
        ]

    @property
    def seeds(self) -> list[str]:
        return [x.strip() for x in self.seed_urls.split(",") if x.strip()]

    @property
    def openhands_allowed_model_list(self) -> list[str]:
        return [x.strip() for x in self.openhands_allowed_models.split(",") if x.strip()]

    @property
    def production_execution_allowed_project_id_list(self) -> list[str]:
        return [
            x.strip()
            for x in self.production_execution_allowed_project_ids.split(",")
            if x.strip()
        ]

    @property
    def production_execution_allowed_team_id_list(self) -> list[str]:
        return [
            x.strip()
            for x in self.production_execution_allowed_team_ids.split(",")
            if x.strip()
        ]

    @property
    def production_execution_denied_project_id_list(self) -> list[str]:
        return [
            x.strip()
            for x in self.production_execution_denied_project_ids.split(",")
            if x.strip()
        ]

settings = Settings()

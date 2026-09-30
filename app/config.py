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

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://assetfactory:assetfactory@postgres:5432/assetfactory"
    redis_url: str = "redis://redis:6379/0"
    user_agent: str = "AI-Digital-Asset-Factory/0.3"
    request_timeout_seconds: int = 20
    max_pages_per_run: int = 20
    seed_urls: str = "https://news.ycombinator.com/,https://github.com/trending"
    github_token: str = ""
    github_discovery_enabled: bool = True
    github_results_per_query: int = 10
    human_approval_key: str = ""
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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def seeds(self) -> list[str]:
        return [x.strip() for x in self.seed_urls.split(",") if x.strip()]

settings = Settings()

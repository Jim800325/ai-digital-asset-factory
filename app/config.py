from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    user_agent: str = "AI-Digital-Asset-Factory/0.1"
    request_timeout_seconds: int = 20
    max_pages_per_run: int = 20
    seed_urls: str = "https://news.ycombinator.com/,https://github.com/trending"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def seeds(self) -> list[str]:
        return [x.strip() for x in self.seed_urls.split(",") if x.strip()]

settings = Settings()

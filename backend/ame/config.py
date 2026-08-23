from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET_KEY = "change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = "development"
    dry_run: bool = True
    log_level: str = "INFO"
    secret_key: str = DEFAULT_SECRET_KEY
    ame_credential_kek: str = ""
    ame_credential_kek_id: str = "v1"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    dashboard_origin: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"

    database_url: str = "postgresql+asyncpg://ame:ame@localhost:5432/ame"
    database_url_sync: str = "postgresql+psycopg://ame:ame@localhost:5432/ame"
    redis_url: str = "redis://localhost:6379/0"

    storage_backend: str = "local"
    storage_local_root: str = "./data/storage"
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    daily_ai_spend_limit: float = 5.0
    daily_media_spend_limit: float = 2.0
    max_content_per_day: int = 6
    max_concurrent_agent_runs: int = 4
    max_research_calls_per_content: int = 4
    minimum_daily_content: int = 0
    target_daily_content: int = 2
    maximum_daily_content: int = 6
    maximum_per_platform: int = 3
    daily_cost_limit: float = 7.0
    autonomous_mode: bool = Field(default=True, validation_alias=AliasChoices("AUTONOMOUS_MODE", "autonomous_mode"))
    owner_timezone: str = Field(default="Europe/Dublin", validation_alias=AliasChoices("OWNER_TIMEZONE", "owner_timezone"))
    scheduler_fast: bool = Field(
        default=False,
        validation_alias=AliasChoices("AME_SCHEDULER_FAST", "SCHEDULER_FAST", "scheduler_fast"),
    )
    bootstrap_simulation: bool = Field(
        default=False,
        validation_alias=AliasChoices("AME_BOOTSTRAP_SIMULATION", "BOOTSTRAP_SIMULATION", "bootstrap_simulation"),
    )
    bootstrap_open_browser: bool = Field(
        default=True,
        validation_alias=AliasChoices("AME_BOOTSTRAP_OPEN_BROWSER", "BOOTSTRAP_OPEN_BROWSER", "bootstrap_open_browser"),
    )

    llm_provider: str = "dev"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "dev-local"
    embedding_model: str = "dev-local"

    tts_provider: str = "dev"
    tts_api_key: str = ""
    tts_voice: str = "default"

    image_provider: str = "none"
    image_api_key: str = ""

    hacker_news_enabled: bool = True
    rss_feeds: str = (
        "https://hnrss.org/frontpage,"
        "https://feeds.bbci.co.uk/news/technology/rss.xml,"
        "https://www.theverge.com/rss/index.xml"
    )
    youtube_data_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "autonomous-media-engine/0.1"

    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = "http://localhost:8000/api/v1/oauth/youtube/callback"

    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/api/v1/oauth/instagram/callback"
    instagram_graph_version: str = "v21.0"

    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = "http://localhost:8000/api/v1/oauth/tiktok/callback"

    default_currency: str = "EUR"

    job_lease_seconds: int = 120
    job_max_attempts: int = 5

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def rss_feed_list(self) -> list[str]:
        return [item.strip() for item in self.rss_feeds.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

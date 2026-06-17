from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_webhook_url: str
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "gemma4:12b"
    ollama_num_ctx: int = 8192
    ollama_temperature: float = 0.0
    ollama_seed: int = 42
    ollama_num_predict: int = 768
    ollama_auto_pull: bool = True
    game_types: str = "wordle,connections"
    schedule_cron: str = "15 0 * * *"
    schedule_tz: str = "America/New_York"
    manual_trigger_enabled: bool = True
    max_invalid_retries: int = 10
    wordle_hard_mode: bool = False
    nyt_user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    nyt_timeout_seconds: float = 10.0
    nyt_max_retries: int = 3
    post_on_fetch_failure: bool = False
    db_path: str = "/data/games.db"
    log_level: str = "INFO"

    @property
    def game_type_list(self) -> list[str]:
        return [g.strip() for g in self.game_types.split(",") if g.strip()]

    def redacted_webhook(self) -> str:
        # keep scheme+host, drop the token segment
        return self.discord_webhook_url.rsplit("/", 1)[0] + "/***"


@lru_cache
def get_settings() -> Settings:
    # discord_webhook_url is populated from the environment by pydantic-settings;
    # mypy cannot see this and reports it as a missing required argument.
    return Settings()  # type: ignore[call-arg]

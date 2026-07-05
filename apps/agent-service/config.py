from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    rag_base_url: str = Field(default="http://localhost:8000", alias="RAG_BASE_URL")
    model: str = Field(default="gpt-4o", alias="AGENT_MODEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

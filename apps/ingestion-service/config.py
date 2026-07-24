# config.py
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

    s3_endpoint: str = Field(..., alias="S3_ENDPOINT")
    s3_access_key: str = Field(..., alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(..., alias="S3_SECRET_KEY")
    s3_bucket: str = Field(..., alias="S3_BUCKET")
    s3_region: str | None = Field(default=None, alias="S3_REGION")

    qdrant_url: str = Field(..., alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")

    jina_api_key: str = Field(..., alias="JINA_API_KEY")
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")

    sync_url: str = Field(default="http://localhost:8003", alias="SYNC_URL")

    rabbitmq_url: str = Field(
        default="amqp://app:app@localhost:5672/",
        alias="RABBITMQ_URL",
    )

    # Model used by /generate for retrieval-grounded answer synthesis. Kept
    # small by default (gpt-4o-mini) — this is a one-shot summarizer, not a
    # tool-calling agent, so a lightweight model is enough.
    generate_model: str = Field(default="gpt-4o-mini", alias="GENERATE_MODEL")

    # Model used to caption extracted figures at ingest time. Runs once per
    # image, so cost is negligible — pick for vision instruction-following,
    # not price. gpt-5-mini resisted the context-contamination failure that
    # gpt-4o-mini exhibited (described the neighbouring premium table
    # instead of the signature in the pixels).
    caption_model: str = Field(default="gpt-5-mini", alias="CAPTION_MODEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # fields populated from env/.env at runtime


if __name__ == "__main__":
    s = get_settings()

    def _mask(v: str | None) -> str:
        if not v:
            return "<MISSING>"
        return f"<set, len={len(v)}>"

    print("S3_ENDPOINT    =", s.s3_endpoint)
    print("S3_BUCKET      =", s.s3_bucket)
    print("S3_ACCESS_KEY  =", _mask(s.s3_access_key))
    print("S3_SECRET_KEY  =", _mask(s.s3_secret_key))
    print("QDRANT_URL     =", s.qdrant_url)
    print("QDRANT_API_KEY =", _mask(s.qdrant_api_key) if s.qdrant_api_key else "<unset (ok if local)>")
    print("JINA_API_KEY   =", _mask(s.jina_api_key))
    print("OPENAI_API_KEY =", _mask(s.openai_api_key))
    print()
    print("OK — all required fields parsed successfully")

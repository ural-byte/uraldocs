from functools import lru_cache
from typing import Literal, Self
from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    postgres_db: str = "uraldocs"
    postgres_user: str = "uraldocs"
    postgres_password: str = ""
    postgres_host: str = "db"
    postgres_port: int = 5432
    app_origin: str = "http://localhost:3000"
    cookie_secure: bool = False
    session_hours: int = Field(default=24, gt=0)
    log_level: str = "INFO"
    kb_mode: Literal["demo", "real_ai"] = "demo"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_embedding_model: str = ""
    ai_chat_model: str = ""
    ai_timeout_seconds: float = Field(default=30, gt=0)
    chat_top_k: int = Field(default=5, ge=1, le=20)
    chat_min_similarity: float = Field(default=0.7, ge=0, le=1)
    chat_max_question_chars: int = Field(default=2000, ge=1, le=10000)
    chat_max_answer_chars: int = Field(default=4000, ge=1, le=20000)
    chat_max_excerpt_chars: int = Field(default=500, ge=1, le=1200)
    worker_lease_seconds: int = Field(default=300, gt=0)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def validate_ai(self) -> Self:
        if self.kb_mode == "real_ai" and not all((self.ai_base_url, self.ai_api_key, self.ai_embedding_model)):
            raise ValueError("Для KB_MODE=real_ai задайте AI_BASE_URL, AI_API_KEY и AI_EMBEDDING_MODEL")
        return self

    @model_validator(mode="after")
    def resolve_database_url(self) -> Self:
        if not self.database_url:
            if not self.postgres_password:
                raise ValueError("Задайте POSTGRES_PASSWORD или DATABASE_URL")
            user = quote(self.postgres_user, safe="")
            password = quote(self.postgres_password, safe="")
            database = quote(self.postgres_db, safe="")
            self.database_url = f"postgresql+psycopg://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{database}"
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

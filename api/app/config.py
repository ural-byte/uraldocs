from functools import lru_cache
from typing import Self
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

"""Shared settings for API and worker."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://pie:pie@localhost:5432/pie"
    redis_url: str = "redis://localhost:6379/0"

    storage_backend: Literal["s3", "fs"] = "s3"
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key: str = "pie_minio"
    s3_secret_key: str = "pie_minio_secret"
    s3_bucket: str = "pie-documents"
    s3_region: str = "us-east-1"
    fs_storage_path: str = "./data/storage"

    gemini_api_key: str = ""
    gemini_classify_model: str = "gemini-2.0-flash"
    gemini_ocr_model: str = "gemini-2.0-flash"

    pie_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    pie_api_key: str = "pie_dev_key_change_me"

    processing_version: str = "1.0"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def use_mock_llm(self) -> bool:
        return not bool(self.gemini_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()

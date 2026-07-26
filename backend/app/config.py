"""
Centralized application configuration.
Loaded once at import time from environment variables / .env file.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: str = "development"
    SECRET_KEY: str = "dev-secret"
    API_KEY_HEADER_NAME: str = "X-API-Key"
    DESKTOP_API_KEY: str = "dev-desktop-key-change-me"

    DATABASE_URL: str = "sqlite:///./transcribeapp.db"

    REDIS_URL: str = "redis://localhost:6379/0"
    QUEUE_NAME: str = "transcription_jobs"

    STORAGE_BACKEND: str = "local"  # local | b2 | s3
    LOCAL_STORAGE_DIR: str = "./storage_data"
    S3_ENDPOINT_URL: str = ""
    S3_BUCKET_NAME: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_REGION: str = "us-west-002"

    SPEECH_PROVIDER: str = "deepgram"  # deepgram | whisper | openrouter | assemblyai | gladia
    DEEPGRAM_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    ASSEMBLYAI_API_KEY: str = ""
    GLADIA_API_KEY: str = ""

    BACKEND_BASE_URL: str = "http://localhost:8000"

    MAX_UPLOAD_SIZE_MB: int = 500
    SUPPORTED_EXTENSIONS: tuple = (
        ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
        ".mp4", ".mov", ".mkv", ".avi",
    )
    VIDEO_EXTENSIONS: tuple = (".mp4", ".mov", ".mkv", ".avi")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

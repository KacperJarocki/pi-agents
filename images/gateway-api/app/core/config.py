import os
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_path: str = os.getenv("DATABASE_PATH", "/data/iot-security.db")
    log_level: str = os.getenv("LOG_LEVEL", "info")
    api_prefix: str = "/api/v1"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

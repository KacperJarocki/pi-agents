import os
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_path: str = os.getenv("DATABASE_PATH", "/data/iot-security.db")
    log_level: str = os.getenv("LOG_LEVEL", "info")
    api_prefix: str = "/api/v1"
    gateway_agent_url: str = os.getenv("GATEWAY_AGENT_URL", "http://gateway-agent.iot-security:7000")
    active_device_window_minutes: int = int(os.getenv("ACTIVE_DEVICE_WINDOW_MINUTES", "15"))
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

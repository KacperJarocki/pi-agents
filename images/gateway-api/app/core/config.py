import os
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Legacy local fallback. New deployments should use the explicit service
    # endpoints below so storage can move outside the Kubernetes cluster.
    database_path: str = os.getenv("DATABASE_PATH", "/data/iot-security.db")
    control_database_url: str = os.getenv(
        "CONTROL_DATABASE_URL", os.getenv("DATABASE_URL", "")
    )
    clickhouse_url: str = os.getenv("CLICKHOUSE_URL", "")
    clickhouse_database: str = os.getenv("CLICKHOUSE_DATABASE", "analytics")
    object_storage_endpoint: str = os.getenv("OBJECT_STORAGE_ENDPOINT", "")
    object_storage_bucket: str = os.getenv("OBJECT_STORAGE_BUCKET", "pi-agents")
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
    event_schema_version: str = os.getenv("EVENT_SCHEMA_VERSION", "v1")
    streaming_enabled: bool = os.getenv("STREAMING_ENABLED", "false").lower() == "true"
    model_path: str = os.getenv("MODEL_PATH", "/data/models")
    log_level: str = os.getenv("LOG_LEVEL", "info")
    api_prefix: str = "/api/v1"
    gateway_agent_url: str = os.getenv("GATEWAY_AGENT_URL", "http://gateway-agent.iot-security:7000")
    active_device_window_minutes: int = int(os.getenv("ACTIVE_DEVICE_WINDOW_MINUTES", "15"))
    
    class Config:
        env_file = ".env"
        case_sensitive = False

    def missing_streaming_endpoints(self) -> list[str]:
        """Return required endpoints missing when the streaming path is enabled."""
        if not self.streaming_enabled:
            return []
        required = {
            "CONTROL_DATABASE_URL": self.control_database_url,
            "CLICKHOUSE_URL": self.clickhouse_url,
            "OBJECT_STORAGE_ENDPOINT": self.object_storage_endpoint,
            "KAFKA_BOOTSTRAP_SERVERS": self.kafka_bootstrap_servers,
        }
        return [name for name, value in required.items() if not value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

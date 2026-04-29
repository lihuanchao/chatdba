from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(Path(".env"), _PROJECT_ROOT_ENV),
        extra="ignore",
    )

    app_env: str = "local"
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str = Field(default="", repr=False)
    qwen_model: str = "qwen-plus"
    qwen_fallback_model: str = "qwen-max"
    qwen_embedding_model: str = "text-embedding-v4"
    dingtalk_client_id: str = ""
    dingtalk_client_secret: str = Field(default="", repr=False)
    dingtalk_stream_enabled: bool = False
    dingtalk_ai_card_template_id: str = ""
    dingtalk_ai_card_content_field: str = "content"
    mysql_connect_timeout_seconds: int = 3
    mysql_query_timeout_seconds: int = 8
    stream_update_interval_ms: int = 1000
    metadata_mysql_host: str = ""
    metadata_mysql_port: int = 3306
    metadata_mysql_user: str = ""
    metadata_mysql_password: str = Field(default="", repr=False)
    metadata_mysql_database: str = ""
    metadata_route_table: str = "table_routes"
    metadata_instance_table: str = "db_instances"

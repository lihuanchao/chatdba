from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    mysql_connect_timeout_seconds: int = 3
    mysql_query_timeout_seconds: int = 8
    stream_update_interval_ms: int = 1000

"""
应用配置模块
使用 pydantic-settings 管理环境变量和配置
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类"""

    # 应用基本信息
    app_name: str = "Search API"
    app_version: str = "0.1.0"
    debug: bool = False

    # API 配置
    api_v1_prefix: str = "/api/v1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# 全局配置实例
settings = Settings()

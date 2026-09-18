"""服务配置（pydantic-settings，读 .env / 环境变量）。

.env 键名与字段同名（大小写不敏感），示例见 L5 的 .env.example：
    PROVIDER=openai_compat            # openai_compat | comfyui | pollinations
    PROVIDER_BASE_URL=https://api.openai.com/v1
    PROVIDER_API_KEY=sk-...
    PROVIDER_MODEL=gpt-image-1
    PROVIDER_TIMEOUT_SECONDS=120
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 可用的推理后端名（配置与 API 请求共用同一取值域）
ProviderName = Literal["openai_compat", "comfyui", "pollinations"]


class Settings(BaseSettings):
    """全局配置。切换 Provider 只改 .env，零代码改动（P1 总验收标准 #1）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 中可能出现非本服务用途的键，忽略
    )

    # 当前启用的推理后端
    provider: ProviderName = "openai_compat"
    # Provider 端点与凭证
    provider_base_url: str = "https://api.openai.com/v1"
    provider_api_key: str = ""
    provider_model: str = "gpt-image-1"
    # 单次 Provider HTTP 请求超时（秒）
    provider_timeout_seconds: float = Field(default=120.0, gt=0)
    # 数据根目录（其下 jobs/ 与 artifacts/，见 ADR-002）
    data_dir: Path = Path("data")
    # 后处理上传图片大小上限（P2 任务书：默认 20MB，可配）
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)


@lru_cache
def get_settings() -> Settings:
    """进程级单例配置。"""
    return Settings()

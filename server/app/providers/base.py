"""Provider 协议与共享契约。

所有推理后端（openai_compat / comfyui / ...）实现同一协议；
路由层与任务执行器只依赖本模块，禁止直接 import 具体 Provider（见 CLAUDE.md 编码规范）。
"""

from typing import Any, Protocol

from pydantic import BaseModel, Field

# ---------- 请求/响应契约（pydantic 锁定，铁律：契约必须用 pydantic 模型定义） ----------


class GenerateImageRequest(BaseModel):
    """文生图请求（Provider 无关的统一契约）。"""

    prompt: str = Field(min_length=1, description="生成提示词")
    size: str = Field(default="1024x1024", pattern=r"^\d+x\d+$", description="宽x高")
    n: int = Field(default=1, ge=1, le=10, description="生成张数")
    # seed 可选透传（P5-L2 动画逐帧生成线需要确定性 seed 序列）：None=不携带，
    # 既有线（P1/P3-L2/P4-L1）行为零变化（None 时 Provider 请求体/GET 参数
    # 均不含 seed 键，测试锁定）；OpenAI images 协议本身支持 seed，透传属对齐
    # 协议而非新功能（任务书交付物 1）
    seed: int | None = Field(default=None, ge=0, description="随机种子（None=不透传）")


class GeneratedImage(BaseModel):
    """单张生成图像（后续服务层确定性处理的输入）。"""

    data: bytes = Field(description="图像原始字节")
    format: str = Field(description="图像格式：png/jpeg/webp/gif/unknown")
    source: str = Field(description="取回方式：b64_json | url，留档排查用")


class GenerationResult(BaseModel):
    """一次生成的完整结果。"""

    images: list[GeneratedImage]
    raw: dict[str, Any] | None = Field(
        default=None, description="响应原始 JSON（已摘除大字段），留档用"
    )


# ---------- 错误分支（任务执行器据此映射 failed 原因与是否可重试） ----------


class ProviderError(Exception):
    """Provider 层错误基类（传输层网络故障等）。"""


class ProviderRequestError(ProviderError):
    """请求被远端拒绝（HTTP 4xx）——通常是参数或凭证问题，重试无效。"""


class ProviderResponseError(ProviderError):
    """远端响应异常（HTTP 5xx 或无法解析的载荷）——可重试。"""


class ProviderTimeoutError(ProviderError):
    """请求超时——可重试。"""


# ---------- 共享工具（确定性处理归服务层，铁律 #3） ----------


def sniff_image_format(data: bytes) -> str:
    """按魔数嗅探图像格式；无法识别返回 "unknown"。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "unknown"


# ---------- Provider 协议 ----------


class Provider(Protocol):
    """推理后端协议（async；完整类型）。"""

    async def generate_image(self, request: GenerateImageRequest) -> GenerationResult:
        """文生图：提交生成请求，取回图像字节结果。"""
        ...

    async def aclose(self) -> None:
        """释放底层 HTTP 资源（实现方自行管理其连接池生命周期）。"""
        ...


class ProviderFactory(Protocol):
    """Provider 工厂协议（路由层/执行器只依赖本协议，不见组合根与具体实现）。"""

    @property
    def default_name(self) -> str:
        """配置默认的 Provider 名（请求未指定覆盖时启用）。"""
        ...

    def get(self, name: str | None = None) -> Provider:
        """按名称取共享实例；未知/未实现的名称抛 ValueError。"""
        ...

    async def aclose(self) -> None:
        """释放全部已构建实例的底层资源（lifespan shutdown 调用）。"""
        ...

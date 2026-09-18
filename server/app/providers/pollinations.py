"""pollinations.ai Provider（GET /prompt/{prompt} 直连，免 key）。

pollinations 的图像 API 不是 OpenAI images 协议：
- `GET {base_url}/prompt/{urlencoded_prompt}?width=&height=&seed=&nologo=true`
- 响应体**直接是图像字节**（JPEG），无 OpenAI 的 `data[]` 包裹、无 b64_json
- 无 key 档：延迟 3-45s 波动；模型由服务端路由（当前为 sana，见 /models）
- seed 固定时输出确定性可复现（md5 一致，2026-09-07 实测）

P1 定位：真实冒烟的临时通道（openai_compat 真实端点就绪前的过渡验证），
正式能力域仍以 ComfyUI / OpenAI 兼容端点为主。
"""

import logging
from typing import Any
from urllib.parse import quote

import httpx

from server.app.core.config import Settings
from server.app.providers.base import (
    GeneratedImage,
    GenerateImageRequest,
    GenerationResult,
    ProviderError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    sniff_image_format,
)

logger = logging.getLogger(__name__)

_ERROR_SNIPPET_LIMIT = 300


def _parse_size(size: str) -> tuple[int, int]:
    """"宽x高" → (width, height)；非法格式抛参数错误。"""
    try:
        width_str, height_str = size.lower().split("x", 1)
        width, height = int(width_str), int(height_str)
    except ValueError as exc:
        raise ProviderRequestError(f"size 格式非法（应为 宽x高）：{size!r}") from exc
    if not (64 <= width <= 2048 and 64 <= height <= 2048):
        raise ProviderRequestError(f"尺寸超出 64-2048 范围：{width}x{height}")
    return width, height


class PollinationsProvider:
    """pollinations.ai GET /prompt 直连实现（满足 Provider 协议）。"""

    def __init__(
        self,
        base_url: str = "https://image.pollinations.ai",
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,  # 防本机代理劫持（STATE.md L4 备注）
            follow_redirects=True,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "PollinationsProvider":
        return cls(timeout_seconds=settings.provider_timeout_seconds)

    async def generate_image(self, request: GenerateImageRequest) -> GenerationResult:
        if request.n != 1:
            raise ProviderRequestError("pollinations 免 key 档仅支持 n=1")
        width, height = _parse_size(request.size)

        params: dict[str, Any] = {
            "width": width,
            "height": height,
            "nologo": "true",
        }
        # seed 可选透传（P5-L2 逐帧生成线）：None 不携带，既有线 GET 请求零变化；
        # seed 固定时 pollinations 输出确定性可复现（2026-09-07 md5 实测）
        if request.seed is not None:
            params["seed"] = request.seed
        url = f"/prompt/{quote(request.prompt, safe='')}"
        try:
            resp = await self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"请求超时：GET {url}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"网络错误：GET {url}: {exc}") from exc

        if resp.status_code >= 500:
            raise ProviderResponseError(
                f"HTTP {resp.status_code}：{resp.text[:_ERROR_SNIPPET_LIMIT]}"
            )
        if resp.status_code >= 400:
            raise ProviderRequestError(
                f"HTTP {resp.status_code}：{resp.text[:_ERROR_SNIPPET_LIMIT]}"
            )

        content_type = resp.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise ProviderResponseError(
                f"响应不是图像（Content-Type={content_type}）："
                f"{resp.text[:_ERROR_SNIPPET_LIMIT]}"
            )

        logger.info("pollinations 生成成功：%d 字节", len(resp.content))
        return GenerationResult(
            images=[
                GeneratedImage(
                    data=resp.content,
                    format=sniff_image_format(resp.content),
                    source="pollinations_get",
                )
            ],
            raw={"model": "pollinations/sana"},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

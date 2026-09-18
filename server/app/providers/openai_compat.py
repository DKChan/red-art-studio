"""OpenAI images generations 兼容 Provider。

对接任意实现 `POST {base_url}/images/generations` 的服务
（OpenAI 官方、国内中转、本地网关等，见 ROADMAP 架构图）。

响应分支：
- `data[i].b64_json` → base64 解码取图像字节
- `data[i].url`      → 再发 GET 下载图像字节
- HTTP 4xx           → ProviderRequestError（参数/凭证问题，重试无效）
- HTTP 5xx / 坏载荷  → ProviderResponseError（可重试）
- 超时 / 传输错误     → ProviderTimeoutError / ProviderError
"""

import base64
import logging
from typing import Any

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

_ERROR_SNIPPET_LIMIT = 500  # 错误信息中保留的响应体长度上限


class OpenAICompatProvider:
    """OpenAI images generations 兼容实现（满足 Provider 协议）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """`client` 供测试注入 MockTransport；不传则自建（并自负关闭）。"""
        self._api_key = api_key
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout_seconds)
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatProvider":
        """从全局配置构建（后续工厂/任务层经此解耦构造参数）。"""
        return cls(
            base_url=settings.provider_base_url,
            api_key=settings.provider_api_key,
            model=settings.provider_model,
            timeout_seconds=settings.provider_timeout_seconds,
        )

    async def generate_image(self, request: GenerateImageRequest) -> GenerationResult:
        """文生图：POST /images/generations，解析 b64_json / url 两种响应。"""
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": request.prompt,
            "n": request.n,
            "size": request.size,
        }
        # seed 可选透传（P5-L2 逐帧生成线；OpenAI images 协议原生支持 seed）：
        # None 不携带请求键，既有线请求体零变化（测试锁定）
        if request.seed is not None:
            payload["seed"] = request.seed
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        resp = await self._request("POST", "/images/generations", json=payload, headers=headers)
        body = self._parse_json_body(resp)

        items = body.get("data")
        if not isinstance(items, list) or not items:
            raise ProviderResponseError(f"响应缺少 data 数组：{body!r}")

        images = [await self._resolve_item(item, idx) for idx, item in enumerate(items)]
        logger.info("openai_compat 生成成功：%d 张图像", len(images))
        return GenerationResult(images=images, raw=_strip_b64(body))

    async def aclose(self) -> None:
        """仅关闭自建的 httpx 客户端（外部注入的由注入方管理）。"""
        if self._owns_client:
            await self._client.aclose()

    # ---------- 内部实现 ----------

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """统一传输层入口：把超时与其他网络错误映射到 Provider 错误分支。"""
        try:
            return await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"请求超时：{method} {url}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"网络错误：{method} {url}: {exc}") from exc

    async def _resolve_item(self, item: Any, idx: int) -> GeneratedImage:
        """解析单个 data 元素：优先 b64_json，其次 url 下载。"""
        if not isinstance(item, dict):
            raise ProviderResponseError(f"data[{idx}] 不是对象：{item!r}")

        b64 = item.get("b64_json")
        if isinstance(b64, str) and b64:
            try:
                # 先剔除可能的换行/空白再严格解码
                data = base64.b64decode("".join(b64.split()), validate=True)
            except ValueError as exc:
                raise ProviderResponseError(f"data[{idx}] b64_json 解码失败：{exc}") from exc
            return GeneratedImage(data=data, format=sniff_image_format(data), source="b64_json")

        url = item.get("url")
        if isinstance(url, str) and url:
            resp = await self._request("GET", url)
            if resp.status_code >= 400:
                raise ProviderResponseError(
                    f"下载图像失败（HTTP {resp.status_code}）：{_snippet(resp.text)}"
                )
            return GeneratedImage(
                data=resp.content, format=sniff_image_format(resp.content), source="url"
            )

        raise ProviderResponseError(f"data[{idx}] 既无 b64_json 也无 url：{item!r}")

    @staticmethod
    def _parse_json_body(resp: httpx.Response) -> dict[str, Any]:
        """校验状态码并解析 JSON 响应体（4xx/5xx/非 JSON 各归其错误分支）。"""
        if resp.status_code >= 400:
            message = f"HTTP {resp.status_code}：{_snippet(resp.text)}"
            if resp.status_code < 500:
                raise ProviderRequestError(message)
            raise ProviderResponseError(message)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderResponseError(f"响应不是合法 JSON：{_snippet(resp.text)!r}") from exc
        if not isinstance(body, dict):
            raise ProviderResponseError(f"响应 JSON 不是对象：{body!r}")
        return body


def _snippet(text: str) -> str:
    """截断错误响应体，避免错误信息爆炸。"""
    return text[:_ERROR_SNIPPET_LIMIT]


def _strip_b64(body: dict[str, Any]) -> dict[str, Any]:
    """摘除响应副本中的 b64 大字段，避免结果模型重复占用内存。"""
    cleaned = dict(body)
    items = cleaned.get("data")
    if isinstance(items, list):
        cleaned["data"] = [
            {k: v for k, v in item.items() if k != "b64_json"} if isinstance(item, dict) else item
            for item in items
        ]
    return cleaned

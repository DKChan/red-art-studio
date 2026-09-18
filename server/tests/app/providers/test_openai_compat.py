"""openai_compat Provider 测试——全部走 httpx.MockTransport，永不真实调用外部服务。

覆盖分支（ROADMAP §P1 L2 验收）：
- 成功 b64_json 响应
- 成功 url 响应（含二次下载）
- HTTP 4xx
- 超时
- 附加：5xx、坏 JSON、缺 data、b64 解码失败、无 b64 也无 url
"""

import base64
import json

import httpx
import pytest

from server.app.core.config import Settings
from server.app.providers.base import (
    GeneratedImage,
    GenerateImageRequest,
    ProviderError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from server.app.providers.openai_compat import OpenAICompatProvider

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _make_provider(handler) -> OpenAICompatProvider:
    """以 MockTransport 构建 Provider（注入的 client 需带与 Provider 相同的 base_url）。"""
    transport = httpx.MockTransport(handler)
    return OpenAICompatProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="gpt-image-1",
        timeout_seconds=1.0,
        client=httpx.AsyncClient(
            base_url="https://api.example.com/v1", transport=transport
        ),
    )


async def _collect(provider: OpenAICompatProvider) -> list[GeneratedImage]:
    result = await provider.generate_image(GenerateImageRequest(prompt="a pixel cat"))
    await provider.aclose()
    return result.images


async def test_generate_b64_json_success() -> None:
    """成功分支 1：data[i].b64_json → 解码为图像字节，格式嗅探为 png。"""

    def handler(request: httpx.Request) -> httpx.Response:
        # 请求契约自检：路径、鉴权头、载荷
        assert request.url.path == "/v1/images/generations"
        assert request.headers["Authorization"] == "Bearer sk-test"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-image-1"
        assert payload["prompt"] == "a pixel cat"
        assert payload["n"] == 1
        assert payload["size"] == "1024x1024"
        # seed 未给（None）→ 请求体不含 seed 键（既有线行为零变化回归）
        assert "seed" not in payload
        return httpx.Response(200, json={"data": [{"b64_json": _b64(PNG_BYTES)}]})

    images = await _collect(_make_provider(handler))
    assert len(images) == 1
    assert images[0].data == PNG_BYTES
    assert images[0].format == "png"
    assert images[0].source == "b64_json"


async def test_generate_seed_in_request_body() -> None:
    """seed 显式给出 → 请求体携带 seed 字段（P5-L2 逐帧生成线透传契约）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["seed"] == 42
        return httpx.Response(200, json={"data": [{"b64_json": _b64(PNG_BYTES)}]})

    provider = _make_provider(handler)
    result = await provider.generate_image(
        GenerateImageRequest(prompt="a pixel cat", seed=42)
    )
    await provider.aclose()
    assert len(result.images) == 1


async def test_generate_url_success() -> None:
    """成功分支 2：data[i].url → 二次 GET 下载图像字节。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/images/generations":
            return httpx.Response(
                200, json={"data": [{"url": "https://cdn.example.com/img/abc.png"}]}
            )
        assert request.url.host == "cdn.example.com"
        assert request.url.path == "/img/abc.png"
        return httpx.Response(200, content=PNG_BYTES)

    images = await _collect(_make_provider(handler))
    assert len(images) == 1
    assert images[0].data == PNG_BYTES
    assert images[0].format == "png"
    assert images[0].source == "url"


async def test_http_4xx_maps_to_request_error() -> None:
    """错误分支：HTTP 4xx → ProviderRequestError（参数/凭证问题，重试无效）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    provider = _make_provider(handler)
    with pytest.raises(ProviderRequestError, match="401"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_timeout_maps_to_timeout_error() -> None:
    """错误分支：请求超时 → ProviderTimeoutError。"""
    import httpx as _httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise _httpx.ConnectTimeout("timed out")

    provider = _make_provider(handler)
    with pytest.raises(ProviderTimeoutError):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_http_5xx_maps_to_response_error() -> None:
    """附加分支：HTTP 5xx → ProviderResponseError（可重试）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="503"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_bad_json_maps_to_response_error() -> None:
    """附加分支：200 但响应体不是 JSON → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="JSON"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_missing_data_array_maps_to_response_error() -> None:
    """附加分支：200 JSON 但缺 data 数组 → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"created": 123})

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="data"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_invalid_b64_maps_to_response_error() -> None:
    """附加分支：b64_json 非法 base64 → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": "!!!not-base64!!!"}]})

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="b64_json"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_item_without_b64_or_url_maps_to_response_error() -> None:
    """附加分支：data 元素既无 b64_json 也无 url → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"revised_prompt": "x"}]})

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="b64_json"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_url_download_failure_maps_to_response_error() -> None:
    """附加分支：url 下载返回 4xx → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/images/generations":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/gone"}]})
        return httpx.Response(404, text="not found")

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="404"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_network_error_maps_to_provider_error() -> None:
    """附加分支：非超时网络错误 → ProviderError 基类。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _make_provider(handler)
    with pytest.raises(ProviderError):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


def test_provider_builds_from_settings() -> None:
    """from_settings 正确传递配置（不真实建连，仅对象构造）。"""
    settings = Settings(
        _env_file=None,
        provider="openai_compat",
        provider_base_url="http://127.0.0.1:9999/v0",
        provider_api_key="sk-x",
        provider_model="some-model",
    )
    provider = OpenAICompatProvider.from_settings(settings)
    # httpx 会给 base_url 自动补尾斜杠，比较时归一化
    assert str(provider._client.base_url).rstrip("/") == "http://127.0.0.1:9999/v0"
    assert provider._model == "some-model"

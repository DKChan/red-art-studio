"""pollinations Provider 测试——全部走 httpx.MockTransport，永不真实调用外部服务。

覆盖分支：
- 成功：响应体直接是图像字节（JPEG 嗅探）
- n>1 → ProviderRequestError（免 key 档契约）
- size 非法 → ProviderRequestError
- HTTP 4xx → ProviderRequestError
- HTTP 5xx → ProviderResponseError
- 超时 → ProviderTimeoutError
- 响应非 image/* Content-Type → ProviderResponseError
"""

import httpx
import pytest

from server.app.providers.base import (
    GenerateImageRequest,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from server.app.providers.pollinations import PollinationsProvider

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body"


def _make_provider(handler) -> PollinationsProvider:
    transport = httpx.MockTransport(handler)
    return PollinationsProvider(
        base_url="https://image.pollinations.ai",
        timeout_seconds=1.0,
        client=httpx.AsyncClient(
            base_url="https://image.pollinations.ai",
            transport=transport,
            trust_env=False,
            follow_redirects=True,
        ),
    )


async def _collect(provider: PollinationsProvider) -> list:
    result = await provider.generate_image(
        GenerateImageRequest(prompt="a pixel cat", size="512x512")
    )
    await provider.aclose()
    return result.images


async def test_generate_success_jpeg() -> None:
    """成功分支：GET /prompt/{prompt}，响应体直接是图像字节。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(b"/prompt/")
        assert b"a%20pixel%20cat" in request.url.raw_path
        assert request.url.params["width"] == "512"
        assert request.url.params["height"] == "512"
        assert request.url.params["nologo"] == "true"
        # seed 未给（None）→ GET 参数不含 seed（既有线行为零变化回归）
        assert "seed" not in request.url.params
        return httpx.Response(200, content=JPEG_BYTES, headers={"content-type": "image/jpeg"})

    images = await _collect(_make_provider(handler))
    assert len(images) == 1
    assert images[0].data == JPEG_BYTES
    assert images[0].format == "jpeg"
    assert images[0].source == "pollinations_get"


async def test_generate_seed_passthrough() -> None:
    """seed 显式给出 → GET 参数携带 seed=<value>（P5-L2 逐帧生成线透传契约）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["seed"] == "42"
        return httpx.Response(200, content=JPEG_BYTES, headers={"content-type": "image/jpeg"})

    provider = _make_provider(handler)
    result = await provider.generate_image(
        GenerateImageRequest(prompt="a pixel cat", size="512x512", seed=42)
    )
    await provider.aclose()
    assert len(result.images) == 1


async def test_reject_n_gt_1() -> None:
    """n>1 → 免 key 档契约：请求前即拒绝，不发网络请求。"""
    provider = _make_provider(lambda request: httpx.Response(500))
    with pytest.raises(ProviderRequestError, match="n=1"):
        await provider.generate_image(
            GenerateImageRequest(prompt="cat", n=2)
        )
    await provider.aclose()


async def test_reject_bad_size_format() -> None:
    """size 非 宽x高 → 请求前即拒绝。

    注：API 层 GenerateImageRequest 的 pydantic pattern 已拦截绝大多数非法
    size；此测试直接验证 Provider 内部 _parse_size 的防御（路由到 Provider
    的调用方可能绕过 API schema，如内部任务）。
    """
    from server.app.providers.pollinations import _parse_size

    with pytest.raises(ProviderRequestError, match="size"):
        _parse_size("1024")
    assert _parse_size("512x512") == (512, 512)


async def test_reject_out_of_range_size() -> None:
    """尺寸超出 64-2048 → 请求前即拒绝。"""
    provider = _make_provider(lambda request: httpx.Response(500))
    with pytest.raises(ProviderRequestError, match="64-2048"):
        await provider.generate_image(
            GenerateImageRequest(prompt="cat", size="32x32")
        )
    await provider.aclose()


async def test_http_4xx_request_error() -> None:
    """HTTP 4xx → ProviderRequestError（参数/凭证问题，重试无效）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text="payment required")

    provider = _make_provider(handler)
    with pytest.raises(ProviderRequestError, match="402"):
        await provider.generate_image(
            GenerateImageRequest(prompt="cat", size="512x512")
        )
    await provider.aclose()


async def test_http_5xx_response_error() -> None:
    """HTTP 5xx → ProviderResponseError（服务端问题，可重试）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="502"):
        await provider.generate_image(
            GenerateImageRequest(prompt="cat", size="512x512")
        )
    await provider.aclose()


async def test_timeout() -> None:
    """超时 → ProviderTimeoutError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    provider = _make_provider(handler)
    with pytest.raises(ProviderTimeoutError):
        await provider.generate_image(
            GenerateImageRequest(prompt="cat", size="512x512")
        )
    await provider.aclose()


async def test_non_image_content_type() -> None:
    """200 但 Content-Type 非 image/* → ProviderResponseError（防 HTML 错误页当图存）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not an image</html>")

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="不是图像"):
        await provider.generate_image(
            GenerateImageRequest(prompt="cat", size="512x512")
        )
    await provider.aclose()

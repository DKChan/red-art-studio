"""comfyui Provider 测试——全部走 httpx.MockTransport，永不真实调用外部服务。

覆盖分支（ROADMAP §P1 L4 验收）：
- 成功：/prompt → /history 首轮未完成次轮完成 → /view 下载 PNG
- n=2：两个 prompt_id 各自流水
- POST /prompt 4xx
- history 轮询超时（注入极小 poll_interval + timeout 加速）
- history status_str=error
- /view 下载 4xx
- from_settings：trust_env=False 与 base_url 归一化
"""

import json
import uuid

import httpx
import pytest

from server.app.core.config import Settings
from server.app.providers.base import (
    GenerateImageRequest,
    ProviderError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from server.app.providers.comfyui import ComfyUIProvider, _build_text2img_workflow

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-comfy-png"
BASE_URL = "http://127.0.0.1:8188"
PROMPT_ID = "aaaa-bbbb-cccc"


def _history_entry(images: list[dict] | None, status_str: str) -> dict:
    """构造一条 history 记录（images=None 表示节点尚无产物）。"""
    outputs = {"7": {"images": images}} if images is not None else {}
    return {
        "prompt": [],
        "outputs": outputs,
        "status": {"status_str": status_str, "completed": status_str == "success"},
    }


def _history_body(entry: dict | None, prompt_id: str = PROMPT_ID) -> dict:
    """构造 /history/{id} 响应体（entry=None 表示 history 里还没有该任务）。"""
    return {} if entry is None else {prompt_id: entry}


def _make_provider(handler, **kwargs) -> ComfyUIProvider:
    """以 MockTransport 构建 Provider（注入的 client 需带与 Provider 相同的 base_url）。"""
    transport = httpx.MockTransport(handler)
    defaults: dict = {"timeout_seconds": 1.0, "poll_interval_seconds": 0.01}
    defaults.update(kwargs)
    client = kwargs.get("client") or httpx.AsyncClient(base_url=BASE_URL, transport=transport)
    defaults.pop("client", None)
    return ComfyUIProvider(base_url=BASE_URL, client=client, **defaults)


async def test_generate_success_with_polling() -> None:
    """成功：首轮 history 未完成，次轮返回 outputs，再经 /view 下载 PNG。"""
    history_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_calls
        if request.url.path == "/prompt":
            # 请求契约自检：workflow JSON 参数化 + client_id
            payload = json.loads(request.content)
            assert payload["client_id"]
            wf = payload["prompt"]
            assert wf["2"]["inputs"]["text"] == "a pixel cat"  # 正向词
            assert wf["4"]["inputs"]["width"] == 1024
            assert wf["4"]["inputs"]["height"] == 1024
            assert 0 <= wf["5"]["inputs"]["seed"] <= 2**32 - 1
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path == f"/history/{PROMPT_ID}":
            history_calls += 1
            if history_calls == 1:
                return httpx.Response(200, json=_history_body(None))
            return httpx.Response(
                200,
                json=_history_body(
                    _history_entry(
                        [
                            {
                                "filename": "red_art_studio_00001_.png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ],
                        "success",
                    )
                ),
            )
        # /view 下载：query 契约自检
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "red_art_studio_00001_.png"
        assert request.url.params["subfolder"] == ""
        assert request.url.params["type"] == "output"
        return httpx.Response(200, content=PNG_BYTES)

    provider = _make_provider(handler)
    result = await provider.generate_image(
        GenerateImageRequest(prompt="a pixel cat", size="1024x1024")
    )
    await provider.aclose()

    assert history_calls == 2
    assert len(result.images) == 1
    image = result.images[0]
    assert image.data == PNG_BYTES
    assert image.format == "png"
    assert image.source == "comfyui_view"
    assert result.raw == {"prompt_id": PROMPT_ID, "status_str": "success"}


async def test_generate_n2_two_prompt_ids() -> None:
    """n=2：两次提交得到不同 prompt_id，各自轮询下载，产出 2 张。"""
    submitted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            payload = json.loads(request.content)
            pid = f"pid-{len(submitted)}"
            submitted.append(payload["client_id"])
            return httpx.Response(200, json={"prompt_id": pid})
        pid = request.url.path.removeprefix("/history/")
        return httpx.Response(
            200,
            json=_history_body(
                _history_entry([{"filename": f"{pid}.png", "subfolder": "", "type": "output"}],
                               "success"),
                prompt_id=pid,
            ),
        )

    def download(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/view"
        return httpx.Response(200, content=PNG_BYTES)

    def router(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/view":
            return download(request)
        return handler(request)

    provider = _make_provider(router)
    result = await provider.generate_image(GenerateImageRequest(prompt="cat", n=2))
    await provider.aclose()

    assert len(result.images) == 2
    assert all(img.source == "comfyui_view" for img in result.images)
    # 两次提交的 prompt_id 互不相同
    assert [e["prompt_id"] for e in result.raw["prompts"]] == ["pid-0", "pid-1"]
    assert result.raw["prompts"][0]["prompt_id"] != result.raw["prompts"][1]["prompt_id"]


async def test_prompt_4xx_maps_to_request_error() -> None:
    """错误分支：POST /prompt 4xx → ProviderRequestError（重试无效）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prompt"
        return httpx.Response(400, json={"error": "validation failed"})

    provider = _make_provider(handler)
    with pytest.raises(ProviderRequestError, match="400"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_history_polling_timeout() -> None:
    """错误分支：history 持续无该 prompt_id 直至轮询上限 → ProviderTimeoutError。"""
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        assert request.url.path == f"/history/{PROMPT_ID}"
        polls += 1
        return httpx.Response(200, json=_history_body(None))

    # 极小超时与轮询间隔，保证测试快速收敛
    provider = _make_provider(handler, timeout_seconds=0.05, poll_interval_seconds=0.01)
    with pytest.raises(ProviderTimeoutError, match=PROMPT_ID):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()
    assert polls >= 1


async def test_history_error_status_maps_to_response_error() -> None:
    """错误分支：history 返回 status_str=error → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        return httpx.Response(
            200, json=_history_body(_history_entry(None, "error"))
        )

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="error"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_view_download_4xx_maps_to_response_error() -> None:
    """错误分支：/view 下载 4xx → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path.startswith("/history/"):
            return httpx.Response(
                200,
                json=_history_body(
                    _history_entry([{"filename": "x.png", "subfolder": "", "type": "output"}],
                                   "success")
                ),
            )
        return httpx.Response(404, text="not found")

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="404"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_prompt_missing_prompt_id_maps_to_response_error() -> None:
    """附加分支：/prompt 响应缺 prompt_id 字段 → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"number": 1})

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="prompt_id"):
        await provider.generate_image(GenerateImageRequest(prompt="p"))
    await provider.aclose()


async def test_history_bad_json_maps_to_response_error() -> None:
    """附加分支：history 返回非 JSON → ProviderResponseError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        return httpx.Response(200, text="<html>oops</html>")

    provider = _make_provider(handler)
    with pytest.raises(ProviderResponseError, match="JSON"):
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


async def test_workflow_template_is_fully_parameterized() -> None:
    """workflow 模板纯函数：prompt/width/height/seed 全部落到节点 inputs。"""
    wf = _build_text2img_workflow(prompt="hi", width=768, height=512, seed=42)
    assert wf["2"]["inputs"]["text"] == "hi"
    assert wf["4"]["inputs"]["width"] == 768
    assert wf["4"]["inputs"]["height"] == 512
    assert wf["5"]["inputs"]["seed"] == 42
    # 节点连线完整性：KSampler 的四个上游引用指向正确节点
    assert wf["5"]["inputs"]["model"] == ["1", 0]
    assert wf["5"]["inputs"]["positive"] == ["2", 0]
    assert wf["5"]["inputs"]["negative"] == ["3", 0]
    assert wf["5"]["inputs"]["latent_image"] == ["4", 0]


def test_provider_builds_from_settings() -> None:
    """from_settings：自建客户端必须 trust_env=False（代理劫持防御），base_url 归一化正确。"""
    settings = Settings(
        _env_file=None,
        provider="comfyui",
        provider_base_url="http://127.0.0.1:8188/",
        provider_timeout_seconds=30.0,
    )
    provider = ComfyUIProvider.from_settings(settings)
    assert provider._client.trust_env is False
    # httpx 会给 base_url 自动补尾斜杠，比较时归一化
    assert str(provider._client.base_url).rstrip("/") == "http://127.0.0.1:8188"
    assert isinstance(provider._client_id, str) and uuid.UUID(provider._client_id)

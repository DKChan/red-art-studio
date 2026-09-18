"""ComfyUI Provider：对接 ComfyUI HTTP API（POST /prompt、GET /history、GET /view）。

文生图流程（每张图一次完整流水，n 张循环 n 次，见 ROADMAP §P1 L4）：
1. 组装 text2img workflow API 图 JSON（CheckpointLoaderSimple → CLIPTextEncode 正/负向 →
   EmptyLatentImage → KSampler → VAEDecode → SaveImage，prompt/尺寸/seed 参数化）
2. POST {base}/prompt 提交，body 为 {prompt: workflow, client_id}，响应取 prompt_id
3. 轮询 GET {base}/history/{prompt_id}（间隔 _POLL_INTERVAL_SECONDS，总时长上限
   timeout_seconds）；完成后从 outputs 收集图像文件清单
4. 逐张 GET {base}/view?filename=&subfolder=&type= 下载图像字节

错误分支与 openai_compat 对齐：
- HTTP 4xx                       → ProviderRequestError（重试无效）
- HTTP 5xx / 坏 JSON / 响应缺字段 → ProviderResponseError（可重试）
- 提交/轮询超时                  → ProviderTimeoutError（可重试）
- 其他网络错误                   → ProviderError

注意（STATE.md「L4 备注」）：本机 shell 的代理环境变量会把 127.0.0.1 流量也交给代理，
自建 httpx.AsyncClient 必须显式 trust_env=False，否则连不上本地 ComfyUI。
"""

import asyncio
import logging
import random
import time
import uuid
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

# 轮询 /history 的间隔（秒）；测试经构造参数注入更小值加速
_POLL_INTERVAL_SECONDS = 0.2
# text2img 默认 checkpoint（标准 ComfyUI 自带；换模型改此常量即可）
_CKPT_NAME = "v1-5-pruned-emaonly.safetensors"
# 负向提示词（通用质量负面词，参照 ComfyUI 默认 text2img 工作流）
_NEGATIVE_PROMPT = "text, watermark, worst quality, low quality, deformed, distorted"
# SaveImage 落盘文件名前缀（ComfyUI 输出目录内）
_FILENAME_PREFIX = "red_art_studio"
# 错误信息中保留的响应体长度上限（与 openai_compat 一致）
_ERROR_SNIPPET_LIMIT = 500
# 采样器随机种子取值域（ComfyUI 常规上限 2^32-1）
_SEED_MAX = 2**32 - 1


class ComfyUIProvider:
    """本地 ComfyUI text2img 实现（满足 Provider 协议）。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
        client_id: str | None = None,
        poll_interval_seconds: float | None = None,
    ) -> None:
        """`client` 供测试注入 MockTransport；`poll_interval_seconds` 供测试加速轮询。"""
        self._client_id = client_id or uuid.uuid4().hex
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = (
            _POLL_INTERVAL_SECONDS if poll_interval_seconds is None else poll_interval_seconds
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            # 代理劫持防御：127.0.0.1 流量不得经代理（STATE.md L4 备注）
            trust_env=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "ComfyUIProvider":
        """从全局配置构建（provider=comfyui 时 PROVIDER_BASE_URL 应指向 8188）。"""
        return cls(
            base_url=settings.provider_base_url,
            timeout_seconds=settings.provider_timeout_seconds,
        )

    async def generate_image(self, request: GenerateImageRequest) -> GenerationResult:
        """文生图：循环 n 次「提交 workflow → 轮询 history → /view 下载」。"""
        width_str, _, height_str = request.size.partition("x")
        width, height = int(width_str), int(height_str)

        images: list[GeneratedImage] = []
        raw_entries: list[dict[str, Any]] = []
        for _ in range(request.n):
            seed = random.randint(0, _SEED_MAX)
            prompt_id, status_str = await self._generate_single(
                prompt=request.prompt, width=width, height=height, seed=seed, images=images
            )
            raw_entries.append({"prompt_id": prompt_id, "status_str": status_str})

        # 单张保持扁平结构，多张收纳为 prompts 列表（留档用）
        raw = raw_entries[0] if len(raw_entries) == 1 else {"prompts": raw_entries}
        logger.info("comfyui 生成成功：%d 张图像", len(images))
        return GenerationResult(images=images, raw=raw)

    async def aclose(self) -> None:
        """仅关闭自建的 httpx 客户端（外部注入的由注入方管理）。"""
        if self._owns_client:
            await self._client.aclose()

    # ---------- 内部实现 ----------

    async def _generate_single(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        images: list[GeneratedImage],
    ) -> tuple[str, str]:
        """单张完整流水：提交 → 轮询 → 下载；返回 (prompt_id, status_str)。"""
        workflow = _build_text2img_workflow(prompt=prompt, width=width, height=height, seed=seed)
        resp = await self._request(
            "POST", "/prompt", json={"prompt": workflow, "client_id": self._client_id}
        )
        body = _parse_json_body(resp)
        prompt_id = body.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ProviderResponseError(f"/prompt 响应缺少 prompt_id：{body!r}")

        specs, status_str = await self._wait_for_outputs(prompt_id)
        for spec in specs:
            images.append(await self._download_image(spec))
        logger.info("comfyui 单张完成：prompt_id=%s，产出 %d 个文件", prompt_id, len(specs))
        return prompt_id, status_str

    async def _wait_for_outputs(self, prompt_id: str) -> tuple[list[dict[str, Any]], str]:
        """轮询 /history 直至出现产物；总时长上限 timeout_seconds，超时抛 ProviderTimeoutError。"""
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            resp = await self._request("GET", f"/history/{prompt_id}")
            body = _parse_json_body(resp)
            entry = body.get(prompt_id)
            if entry is not None:
                outcome = _entry_image_specs(entry)
                if outcome is not None:
                    return outcome
            if time.monotonic() >= deadline:
                raise ProviderTimeoutError(
                    f"轮询 /history 超时：prompt_id={prompt_id} "
                    f"在 {self._timeout_seconds}s 内未完成"
                )
            await asyncio.sleep(self._poll_interval_seconds)

    async def _download_image(self, spec: dict[str, Any]) -> GeneratedImage:
        """按 history 给出的文件清单从 /view 下载单张图像字节。"""
        params = {
            "filename": spec["filename"],
            "subfolder": spec.get("subfolder") or "",
            # history 图像条目的类型字段名即 type（对应 /view 同名 query 参数）
            "type": spec.get("type") or "output",
        }
        resp = await self._request("GET", "/view", params=params)
        if resp.status_code >= 400:
            # 下载阶段无论 4xx/5xx 都按响应异常处理（与 openai_compat 的 url 下载一致）
            raise ProviderResponseError(
                f"下载图像失败（HTTP {resp.status_code}）：{_snippet(resp.text)}"
            )
        return GeneratedImage(
            data=resp.content, format=sniff_image_format(resp.content), source="comfyui_view"
        )

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """统一传输层入口：把超时与其他网络错误映射到 Provider 错误分支。"""
        try:
            return await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"请求超时：{method} {url}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"网络错误：{method} {url}: {exc}") from exc


# ---------- workflow 模板与 history 解析（无 IO，纯函数） ----------


def _build_text2img_workflow(*, prompt: str, width: int, height: int, seed: int) -> dict[str, Any]:
    """组装 text2img workflow API 图 JSON；节点引用格式为 [节点id, 输出槽位]。"""
    return {
        # checkpoint 加载：模型、CLIP、VAE 的共同来源
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _CKPT_NAME}},
        # 正向 = 用户 prompt，负向 = 通用负面词
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 0]}},
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": _NEGATIVE_PROMPT, "clip": ["1", 0]},
        },
        # 空潜空间：尺寸与 seed 在此参数化；batch_size 固定 1，多张由 Provider 层循环
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        # 采样（采样器/步数等先用 ComfyUI 默认工作流取值，后续需求再参数化）
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 20,
                "cfg": 8.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        # 潜空间解码回像素
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        # 落盘到 ComfyUI 输出目录（之后经 /view 取回）
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": _FILENAME_PREFIX, "images": ["6", 0]},
        },
    }


def _entry_image_specs(entry: Any) -> tuple[list[dict[str, Any]], str] | None:
    """从单条 history 记录提取图像清单。

    返回 (图像条目列表, status_str)；任务尚未完成返回 None（继续轮询）。
    出错态（images 为空且 status_str != "success"）抛 ProviderResponseError。
    """
    if not isinstance(entry, dict):
        raise ProviderResponseError(f"history 记录不是对象：{entry!r}")
    status = entry.get("status")
    status_str = status.get("status_str", "") if isinstance(status, dict) else ""

    specs: list[dict[str, Any]] = []
    outputs = entry.get("outputs")
    if isinstance(outputs, dict):
        for node in outputs.values():
            if not isinstance(node, dict) or not isinstance(node.get("images"), list):
                continue
            for item in node["images"]:
                if not isinstance(item, dict) or not item.get("filename"):
                    raise ProviderResponseError(f"images 条目缺少 filename：{item!r}")
                specs.append(item)

    if specs:
        return specs, status_str
    if status_str != "success":
        # error / 缺 status 字段等异常态：不等待，直接报错
        raise ProviderResponseError(f"ComfyUI 生成失败（status_str={status_str!r}）")
    # success 但尚无产物：按未完成处理，继续轮询直至超时
    return None


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

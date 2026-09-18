"""textures API 端到端测试（P3-L2 验收 1：路由分支 + 全链路 + final_outputs 契约）。

形态：fake Provider（内存脚本化成功/4xx/5xx/超时）→ 真 TextureJobRunner（重试
逻辑真实执行）→ 真文件系统存储 → TestClient 驱动真实路由，零真实外呼。
必须用 `with TestClient(...)`：上下文持常驻 portal/事件循环并跑 lifespan
（P2 实测：裸实例每请求独立 loop，后台任务永卡 running）。
"""

import io
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app.core.config import Settings
from server.app.core.storage import FileSystemJobStore
from server.app.jobs.executor import TextureJobRunner
from server.app.main import create_app
from server.app.providers.base import (
    GeneratedImage,
    GenerationResult,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    sniff_image_format,
)

_SIZE = 64


# ---------- fake Provider（脚本化失败序列，重试逻辑真实执行） ----------


class FakeProvider:
    """满足 Provider 协议的内存假后端：按脚本依次抛错，脚本耗尽后成功。"""

    def __init__(self, failures: list[Exception], image: bytes) -> None:
        self._failures = list(failures)
        self._image = image
        self.calls = 0

    async def generate_image(self, request: Any) -> GenerationResult:
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return GenerationResult(
            images=[
                GeneratedImage(
                    data=self._image,
                    format=sniff_image_format(self._image),
                    source="fake",
                )
            ]
        )

    async def aclose(self) -> None:
        return None


class FakeFactory:
    """返回固定 FakeProvider 的工厂协议实现。"""

    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider

    @property
    def default_name(self) -> str:
        return "fake"

    def get(self, name: str | None = None) -> FakeProvider:
        return self._provider

    async def aclose(self) -> None:
        return None


# ---------- 夹具 ----------


def _gradient_png(size: int = 512) -> bytes:
    """平滑对角渐变源图（低频内容，检缝可通过；斜率标定见 test_texture_pipeline）。"""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            v = min(255, (x + y) * 2 * _SIZE // size)
            px[x, y] = (v, v, v)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@contextmanager
def _client(
    tmp_path: Path, provider: FakeProvider
) -> Iterator[tuple[TestClient, Path]]:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    store = FileSystemJobStore(settings.data_dir)
    app = create_app(settings=settings, factory=FakeFactory(provider), store=store)
    app.state.texture_runner = TextureJobRunner(store, FakeFactory(provider))
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/textures/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"任务在 {timeout}s 内未到达终态")


def _decode(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


# ---------- 成功路径 ----------


def test_submit_poll_succeeded_full_contract(tmp_path: Path) -> None:
    """submit → 轮询 succeeded → 产物三件 + seam_report 数值 + final_outputs 契约。"""
    provider = FakeProvider([], _gradient_png())
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post(
            "/api/v1/textures", json={"prompt": "seamless pixel art grass texture"}
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        job_id = body["job_id"]
        assert len(job_id) == 32

        state = _poll_until_terminal(client, job_id)
        assert state["status"] == "succeeded"
        assert state["params"]["prompt"] == "seamless pixel art grass texture"
        assert state["params"]["quantize"] is False
        assert state["params"]["isometric"] is False
        assert state["params"]["kind"] == "texture"
        assert state["error"] is None

        # 产物清单：正图 + 预览（无等距）
        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == ["texture.png", "tiling_preview.png"]

        # 检缝报告随状态响应交付且 passed（低频渐变夹具）
        report = state["seam_report"]
        assert report is not None
        assert report["passed"] is True
        assert report["horizontal_max_step"] < 6
        assert report["vertical_max_step"] < 6

        # 磁盘产物：正图 64×64、预览 192×192
        texture = _decode((data_dir / "artifacts" / job_id / "texture.png").read_bytes())
        preview = _decode(
            (data_dir / "artifacts" / job_id / "tiling_preview.png").read_bytes()
        )
        assert texture.size == (_SIZE, _SIZE)
        assert preview.size == (192, 192)

        # final_outputs.json 契约：三产物字段 + seam_report，宽度/高度真实
        final = json.loads(
            (data_dir / "artifacts" / job_id / "final_outputs.json").read_text("utf-8")
        )
        assert set(final) == {"job_id", "created_at", "outputs", "seam_report"}
        assert final["job_id"] == job_id
        by_name = {o["filename"]: o for o in final["outputs"]}
        assert by_name["texture.png"]["width"] == 64
        assert by_name["texture.png"]["height"] == 64
        assert by_name["tiling_preview.png"]["width"] == 192
        assert by_name["tiling_preview.png"]["height"] == 192
        assert final["seam_report"]["passed"] is True

        # job.json 契约：kind=texture 落盘
        job_record = json.loads(
            (data_dir / "jobs" / job_id / "job.json").read_text("utf-8")
        )
        assert job_record["params"]["kind"] == "texture"
        assert job_record["status"] == "succeeded"


def test_isometric_variant_end_to_end(tmp_path: Path) -> None:
    """isometric=true → 追加 128×64 等距产物（四角透明）。"""
    provider = FakeProvider([], _gradient_png())
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post(
            "/api/v1/textures",
            json={"prompt": "isometric grass", "isometric": True, "quantize": True},
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        state = _poll_until_terminal(client, job_id)
        assert state["status"] == "succeeded"
        assert state["params"]["isometric"] is True
        assert state["params"]["quantize"] is True
        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == [
            "texture.png",
            "tiling_preview.png",
            "isometric_texture.png",
        ]

        iso = _decode(
            (data_dir / "artifacts" / job_id / "isometric_texture.png").read_bytes()
        )
        assert iso.size == (128, 64)
        px = iso.load()
        # 四角透明（菱形外）
        assert px[0, 0][3] == 0
        assert px[127, 63][3] == 0
        # 中心不透明（菱形内）
        assert px[64, 32][3] == 255


def test_seam_failure_reported_not_failed(tmp_path: Path) -> None:
    """检缝不过不判 failed：硬缝纹理照常 succeeded，报告如实 passed=False。"""
    # 左黑右白硬缝源图（8× 缩放前即硬缝，降采样不消除）
    img = Image.new("RGB", (512, 512))
    px = img.load()
    for y in range(512):
        for x in range(512):
            px[x, y] = (0, 0, 0) if x < 256 else (255, 255, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    provider = FakeProvider([], buf.getvalue())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post("/api/v1/textures", json={"prompt": "hard seam"})
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        assert state["seam_report"]["passed"] is False
        assert state["seam_report"]["horizontal_max_step"] > 6


# ---------- 失败分支与重试 ----------


def test_provider_4xx_fails_immediately_without_retry(tmp_path: Path) -> None:
    """4xx → failed 不可重试：Provider 恰被调用 1 次。"""
    provider = FakeProvider([ProviderRequestError("HTTP 401：bad key")], _gradient_png())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post("/api/v1/textures", json={"prompt": "x"})
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "failed"
        assert "ProviderRequestError" in state["error"]
        assert provider.calls == 1


@pytest.mark.parametrize(
    "failure",
    [
        ProviderResponseError("HTTP 502：bad gateway"),
        ProviderTimeoutError("请求超时"),
    ],
    ids=["5xx", "timeout"],
)
def test_provider_retryable_failures_retry_then_succeed(
    tmp_path: Path, failure: Exception
) -> None:
    """5xx/超时 → 重试（脚本化失败 1 次）→ 第 2 次成功，任务 succeeded。"""
    provider = FakeProvider([failure], _gradient_png())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post("/api/v1/textures", json={"prompt": "x"})
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        assert provider.calls == 2


def test_provider_retry_exhausted_fails(tmp_path: Path) -> None:
    """连续 5xx 耗尽重试（1+2 次）→ failed，error 保留最后一次。"""
    provider = FakeProvider(
        [
            ProviderResponseError("HTTP 500：1st"),
            ProviderResponseError("HTTP 503：2nd"),
            ProviderResponseError("HTTP 502：3rd"),
        ],
        _gradient_png(),
    )
    with _client(tmp_path, provider) as (client, _):
        resp = client.post("/api/v1/textures", json={"prompt": "x"})
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "failed"
        assert provider.calls == 3
        assert "502" in state["error"]


# ---------- 请求校验（422/404） ----------


def test_submit_validation_422(tmp_path: Path) -> None:
    """空 prompt / 超长 prompt / extra 字段 / 非 JSON → 422。"""
    provider = FakeProvider([], _gradient_png())
    with _client(tmp_path, provider) as (client, _):
        # 空 prompt
        assert client.post("/api/v1/textures", json={"prompt": ""}).status_code == 422
        # 超 2000 字符
        assert (
            client.post(
                "/api/v1/textures", json={"prompt": "x" * 2001}
            ).status_code
            == 422
        )
        # extra 字段（extra=forbid，P2 教训）
        assert (
            client.post(
                "/api/v1/textures",
                json={"prompt": "x", "seed": 42},
            ).status_code
            == 422
        )
        # 缺 prompt
        assert client.post("/api/v1/textures", json={}).status_code == 422


def test_get_unknown_job_404(tmp_path: Path) -> None:
    """未知 job_id（含穿越形态）→ 404。"""
    provider = FakeProvider([], _gradient_png())
    with _client(tmp_path, provider) as (client, _):
        assert (
            client.get("/api/v1/textures/ffffffffffffffffffffffffffffffff").status_code
            == 404
        )
        assert client.get("/api/v1/textures/..%2F..%2Fetc").status_code == 404

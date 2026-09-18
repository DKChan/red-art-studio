"""animate API 端到端测试（P5-L2 验收 1：契约/重试/路由/final_outputs 键集）。

形态：fake Provider（内存脚本化成功/4xx/5xx/超时，可记录逐帧请求）→ 真
AnimateJobRunner（整帧序列级重试逻辑真实执行）→ 真文件系统存储 → TestClient
驱动真实路由，零真实外呼。必须用 `with TestClient(...)`（P2 实测纪律）。

夹具全部 Pillow 代码生成；断言全部数值取证（回读 n_frames/loop/seed 序列），
不读图。
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
from server.app.jobs.executor import AnimateJobRunner
from server.app.main import create_app
from server.app.providers.base import (
    GeneratedImage,
    GenerationResult,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
)

# ---------- fake Provider（脚本化失败序列，整帧序列级重试逻辑真实执行） ----------


class FakeProvider:
    """满足 Provider 协议的内存假后端：按脚本依次抛错，脚本耗尽后成功。

    每次调用返回可区分的帧（调用序号编码进像素），供逐帧断言；requests 留档。
    """

    def __init__(self, failures: list[Exception], size: tuple[int, int] = (64, 64)) -> None:
        self._failures = list(failures)
        self._size = size
        self.calls = 0
        self.requests: list[Any] = []

    async def generate_image(self, request: Any) -> GenerationResult:
        self.calls += 1
        self.requests.append(request)
        if self._failures:
            raise self._failures.pop(0)
        # 每次调用生成调用序号编码的帧（帧间可区分，防编码器合并）
        img = Image.new("RGBA", self._size, (255, 0, 0, 255))
        px = img.load()
        v = self.calls % self._size[0]
        for y in range(self._size[1]):
            px[v, y] = (0, 0, 255, 255)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return GenerationResult(
            images=[
                GeneratedImage(data=buf.getvalue(), format="png", source="fake")
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


@contextmanager
def _client(tmp_path: Path, provider: FakeProvider) -> Iterator[tuple[TestClient, Path]]:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    store = FileSystemJobStore(settings.data_dir)
    app = create_app(settings=settings, factory=FakeFactory(provider), store=store)
    app.state.animate_runner = AnimateJobRunner(store, FakeFactory(provider))
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/animate/{job_id}")
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


# ---------- 成功路径（端到端） ----------


def test_submit_poll_succeeded_webp_full_contract(tmp_path: Path) -> None:
    """submit → 轮询 succeeded → webp 产物 + animate_report 全字段 + 契约键集。"""
    provider = FakeProvider([])
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post(
            "/api/v1/animate",
            json={
                "prompt": "a small red ball bouncing",
                "animation_type": "idle",
                "frame_count": 4,
                "size": "64x64",
                "seed": 42,
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        job_id = body["job_id"]

        state = _poll_until_terminal(client, job_id)
        assert state["status"] == "succeeded"
        assert state["error"] is None

        # params 回显
        params = state["params"]
        assert params["kind"] == "animate"
        assert params["prompt"] == "a small red ball bouncing"
        assert params["animation_type"] == "idle"
        assert params["frame_count"] == 4
        assert params["size"] == "64x64"
        assert params["seed"] == 42

        # 逐帧请求断言：4 次调用、prompt 模板帧序递增、seed 线性序列透传
        assert provider.calls == 4
        seeds = [req.seed for req in provider.requests]
        assert seeds == [42, 43, 44, 45]
        assert "frame 1 of 4" in provider.requests[0].prompt
        assert "frame 4 of 4" in provider.requests[3].prompt
        assert "idle breathing" in provider.requests[0].prompt

        # 产物清单：webp 单产物
        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == ["animation.webp"]

        # animate_report 回显（含 L1 pack 内嵌）
        report = state["animate_report"]
        assert report is not None
        assert report["frame_count"] == 4
        assert report["size"] == [64, 64]
        assert report["seeds"] == [42, 43, 44, 45]
        assert len(report["frame_prompts"]) == 4
        assert report["animation_type"] == "idle"
        assert report["pixel"] is False
        assert report["alpha_mode"] == "soft"  # None + pixel=false → soft 路由
        pack = report["pack"]
        assert pack["loop_report"]["passed"] is not None
        assert "sheet_meta" not in pack  # webp 线 None 省略

        # 磁盘 webp 回读：n_frames=4、loop=0
        webp = _decode((data_dir / "artifacts" / job_id / "animation.webp").read_bytes())
        assert getattr(webp, "n_frames", 1) == 4
        assert webp.info.get("loop") == 0

        # final_outputs.json 契约：animate_report 键存在且与响应一致
        final = json.loads(
            (data_dir / "artifacts" / job_id / "final_outputs.json").read_text("utf-8")
        )
        assert set(final) == {"job_id", "created_at", "outputs", "animate_report"}
        assert final["animate_report"]["seeds"] == [42, 43, 44, 45]

        # job.json 契约：kind=animate 落盘
        job_record = json.loads(
            (data_dir / "jobs" / job_id / "job.json").read_text("utf-8")
        )
        assert job_record["params"]["kind"] == "animate"
        assert job_record["status"] == "succeeded"


def test_spritesheet_line_end_to_end(tmp_path: Path) -> None:
    """spritesheet 线：sheet.png + spritesheet.json 双产物 + pack.sheet_meta。"""
    provider = FakeProvider([])
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post(
            "/api/v1/animate",
            json={
                "prompt": "a ball",
                "frame_count": 4,
                "size": "64x64",
                "output_format": "spritesheet",
                "pixel": True,
            },
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"

        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == ["sheet.png", "spritesheet.json"]
        assert state["animate_report"]["alpha_mode"] == "sharp"  # None+pixel → sharp

        sheet = _decode((data_dir / "artifacts" / resp.json()["job_id"] / "sheet.png").read_bytes())
        assert sheet.size == (128, 128)  # 2×2 网格 × 64×64 帧

        meta = json.loads(
            (data_dir / "artifacts" / resp.json()["job_id"] / "spritesheet.json").read_text(
                "utf-8"
            )
        )
        assert meta["frame_size"] == [64, 64]
        assert meta["columns"] == 2
        assert meta["rows"] == 2
        assert meta["loop"] is True


def test_final_outputs_backward_compatible_key_set(tmp_path: Path) -> None:
    """animate_report=None 序列化省略：非动画线 final_outputs.json 键集不变。"""
    from server.app.core.storage import FileSystemJobStore as _Store

    store = _Store(tmp_path)
    from server.app.jobs.models import FinalOutputs, OutputFile, utc_now_iso

    final = FinalOutputs(
        job_id="0" * 32,
        created_at=utc_now_iso(),
        outputs=[
            OutputFile(filename="000.png", width=8, height=8, format="png"),
        ],
    )
    store.save_final_outputs(final)
    data = json.loads((tmp_path / "artifacts" / ("0" * 32) / "final_outputs.json").read_text())
    # P1 三键形态（无 seam_report/ui_components/anim_report/animate_report 键）
    assert set(data) == {"job_id", "created_at", "outputs"}


# ---------- 整帧序列级重试 ----------


def test_provider_4xx_fails_whole_sequence_without_retry(tmp_path: Path) -> None:
    """4xx → 整单 failed 不可重试：Provider 恰被调用 1 次（帧级不部分重试）。"""
    provider = FakeProvider([ProviderRequestError("HTTP 401：bad key")])
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/animate", json={"prompt": "x", "frame_count": 4, "size": "64x64"}
        )
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
def test_retryable_failure_retries_whole_sequence_then_succeeds(
    tmp_path: Path, failure: Exception
) -> None:
    """5xx/超时 → 整串重试（从头重生成）→ 第 2 轮成功：调用数=1(败)+4(成)。"""
    provider = FakeProvider([failure])
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/animate", json={"prompt": "x", "frame_count": 4, "size": "64x64"}
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        # 整帧序列级：首轮第 1 帧失败（1 次调用）+ 重试整串成功（4 次调用）
        assert provider.calls == 5
        assert len(state["animate_report"]["seeds"]) == 4


def test_retry_exhausted_fails(tmp_path: Path) -> None:
    """连续 5xx 耗尽重试（1+2 轮）→ failed，error 保留最后一次。"""
    provider = FakeProvider(
        [
            ProviderResponseError("HTTP 500：r1f1"),
            ProviderResponseError("HTTP 503：r2f1"),
            ProviderResponseError("HTTP 502：r3f1"),
        ]
    )
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/animate", json={"prompt": "x", "frame_count": 4, "size": "64x64"}
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "failed"
        assert provider.calls == 3
        assert "502" in state["error"]


def test_frame_size_mismatch_job_failed(tmp_path: Path) -> None:
    """provider 返回尺寸与请求不符 → 管线拒绝（诚实失败）→ job failed 不悬空。"""
    provider = FakeProvider([], size=(16, 16))  # 帧实际 16×16，请求 64×64
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/animate", json={"prompt": "x", "frame_count": 4, "size": "64x64"}
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "failed"
        assert "尺寸与请求不符" in state["error"]


# ---------- 请求校验（422/404） ----------


def _submit(client: TestClient, body: dict) -> int:
    """POST /api/v1/animate 的状态码快捷方式（422 组断言用）。"""
    return client.post("/api/v1/animate", json=body).status_code


def test_submit_validation_422(tmp_path: Path) -> None:
    """契约 422 组：帧数域/prompt 长度/枚举外/extra 字段/pixel 画布/color_count 交叉。"""
    provider = FakeProvider([])
    with _client(tmp_path, provider) as (client, _):
        # 帧数域：3（<4）/5（奇数）/17（>16）→ 422；4/16 边界通过受理（202）
        assert _submit(client, {"prompt": "x", "frame_count": 3}) == 422
        assert _submit(client, {"prompt": "x", "frame_count": 5}) == 422
        assert _submit(client, {"prompt": "x", "frame_count": 17}) == 422
        assert _submit(client, {"prompt": "x", "frame_count": 4}) == 202
        assert _submit(client, {"prompt": "x", "frame_count": 16}) == 202
        # prompt 长度域
        assert client.post("/api/v1/animate", json={"prompt": ""}).status_code == 422
        assert client.post("/api/v1/animate", json={"prompt": "x" * 501}).status_code == 422
        # 枚举外
        assert (
            client.post(
                "/api/v1/animate", json={"prompt": "x", "animation_type": "dance"}
            ).status_code
            == 422
        )
        # extra 字段（extra=forbid）
        assert (
            client.post(
                "/api/v1/animate", json={"prompt": "x", "padding": 16}
            ).status_code
            == 422
        )
        # pixel=true 画布 257 拒绝 / 256 通过
        assert (
            client.post(
                "/api/v1/animate",
                json={"prompt": "x", "frame_count": 4, "size": "257x256", "pixel": True},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/animate",
                json={"prompt": "x", "frame_count": 4, "size": "256x256", "pixel": True},
            ).status_code
            == 202
        )
        # pixel=false 不设画布上限（512x512 通过）
        assert client.post(
            "/api/v1/animate", json={"prompt": "x", "frame_count": 4, "size": "64x64"}
        ).status_code == 202
        # color_count 无 pixel 携带 422
        assert (
            client.post(
                "/api/v1/animate",
                json={"prompt": "x", "frame_count": 4, "color_count": 16},
            ).status_code
            == 422
        )
        # 尺寸域（64-2048）
        assert (
            client.post(
                "/api/v1/animate", json={"prompt": "x", "frame_count": 4, "size": "32x64"}
            ).status_code
            == 422
        )


def test_get_unknown_job_404(tmp_path: Path) -> None:
    """未知 job_id（含穿越形态）→ 404。"""
    provider = FakeProvider([])
    with _client(tmp_path, provider) as (client, _):
        assert (
            client.get("/api/v1/animate/ffffffffffffffffffffffffffffffff").status_code == 404
        )
        assert client.get("/api/v1/animate/..%2F..%2Fetc").status_code == 404

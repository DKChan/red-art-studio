"""ui_gen API 端到端测试（P4-L1 generate / P4-L2 extract）。

形态：fake Provider（内存脚本化成功/4xx/5xx/超时）→ 真 UiGenJobRunner（重试
逻辑真实执行）→ 真文件系统存储 → TestClient 驱动真实路由，零真实外呼。
必须用 `with TestClient(...)`：上下文持常驻 portal/事件循环并跑 lifespan
（P2 实测：裸实例每请求独立 loop，后台任务永卡 running）。

L2 extract：multipart 防线（0 张/超 8 张 422 硬门禁、413、坏文件）+ 端到端
（零 provider 调用实证、双坐标、确定性字节一致、components.json 含 source 字段）。
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
from server.app.jobs.executor import UiGenJobRunner
from server.app.main import create_app
from server.app.providers.base import (
    GeneratedImage,
    GenerationResult,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    sniff_image_format,
)

# ---------- fake Provider（脚本化失败序列，重试逻辑真实执行） ----------


class FakeProvider:
    """满足 Provider 协议的内存假后端：按脚本依次抛错，脚本耗尽后成功。"""

    def __init__(self, failures: list[Exception], image: bytes) -> None:
        self._failures = list(failures)
        self._image = image
        self.calls = 0
        self.last_size: str | None = None

    async def generate_image(self, request: Any) -> GenerationResult:
        self.calls += 1
        self.last_size = request.size
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


def _ui_sheet_png(width: int = 128, height: int = 128) -> bytes:
    """灰 matte 底 + 两个分离 UI 色块的"provider 出图"夹具（去背后 2 组件）。"""
    img = Image.new("RGB", (width, height), (204, 204, 204))
    px = img.load()
    for y in range(10, 50):
        for x in range(10, 60):
            px[x, y] = (180, 40, 40)
    for y in range(70, 110):
        for x in range(60, 110):
            px[x, y] = (40, 40, 180)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@contextmanager
def _client(tmp_path: Path, provider: FakeProvider) -> Iterator[tuple[TestClient, Path]]:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    store = FileSystemJobStore(settings.data_dir)
    app = create_app(settings=settings, factory=FakeFactory(provider), store=store)
    app.state.ui_gen_runner = UiGenJobRunner(store, FakeFactory(provider))
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/ui_gen/{job_id}")
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


# ---------- 成功路径（全契约） ----------


def test_submit_poll_succeeded_full_contract(tmp_path: Path) -> None:
    """submit → 轮询 succeeded → sheet.png + components.json + 契约全字段。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post(
            "/api/v1/ui_gen",
            json={"prompt": "pixel art game UI button set"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        job_id = body["job_id"]
        assert len(job_id) == 32

        state = _poll_until_terminal(client, job_id)
        assert state["status"] == "succeeded"
        assert state["error"] is None
        # params 契约：缺省值 + kind 落盘
        assert state["params"]["kind"] == "ui_gen"
        assert state["params"]["prompt"] == "pixel art game UI button set"
        assert state["params"]["quality"] == "detailed"
        assert state["params"]["resolution"] == "2k"
        assert state["params"]["aspect_ratio"] == "1:1"
        assert state["params"]["background_color"] == "#cccccc"
        assert state["params"]["remove_background"] is True
        assert state["params"]["split_components"] is True

        # 产物清单：透明聚合表 + components.json
        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == ["sheet.png", "components.json"]

        # ui_components 随状态响应交付且门禁通过
        manifest = state["ui_components"]
        assert manifest is not None
        assert manifest["gate"]["passed"] is True
        assert manifest["gate"]["component_count"] == 2
        assert len(manifest["components"]) == 2
        assert manifest["actual_size"] == [128, 128]
        assert manifest["components"][0]["label"] == "component_01"

        # 磁盘产物：sheet.png 带透明像素（matte 被抠掉）
        sheet = _decode((data_dir / "artifacts" / job_id / "sheet.png").read_bytes())
        assert sheet.mode == "RGBA"
        px = sheet.load()
        assert px[0, 0][3] == 0
        assert px[30, 30][3] == 255

        # 磁盘 components.json 与状态响应一致
        manifest_disk = json.loads(
            (data_dir / "artifacts" / job_id / "components.json").read_text("utf-8")
        )
        assert manifest_disk["gate"]["component_count"] == 2
        assert manifest_disk["actual_size"] == [128, 128]

        # final_outputs.json 契约：UI 线四键（三键 + ui_components）
        final = json.loads(
            (data_dir / "artifacts" / job_id / "final_outputs.json").read_text("utf-8")
        )
        assert set(final) == {"job_id", "created_at", "outputs", "ui_components"}
        by_name = {o["filename"]: o for o in final["outputs"]}
        assert by_name["sheet.png"]["width"] == 128
        assert by_name["sheet.png"]["height"] == 128
        # components.json 非图像：尺寸 0（诚实语义），实际尺寸在 actual_size
        assert by_name["components.json"]["width"] == 0
        assert by_name["components.json"]["height"] == 0

        # artifacts 端点可取回 components.json（在 final_outputs 清单内）
        artifacts_resp = client.get(f"/api/v1/artifacts/{job_id}/components.json")
        assert artifacts_resp.status_code == 200
        assert artifacts_resp.headers["content-type"].startswith("application/json")


def test_provider_called_with_resolution_size(tmp_path: Path) -> None:
    """provider 收到的 size 来自档位映射（1k/16:9 → 1024x576；推断约定）。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/ui_gen",
            json={"prompt": "hud icons", "resolution": "1k", "aspect_ratio": "16:9"},
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        assert provider.last_size == "1024x576"


def test_remove_background_false_forced_none_end_to_end(tmp_path: Path) -> None:
    """remove_background=false → 不去背：sheet 无透明像素（强制 none 的行为断言）。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/ui_gen",
            json={"prompt": "x", "remove_background": False},
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        sheet = _decode(
            (tmp_path / "artifacts" / state["job_id"] / "sheet.png").read_bytes()
        )
        px = sheet.load()
        assert px[0, 0][3] == 255  # matte 原样保留
        assert sheet.getextrema()[3][0] == 255  # 全图无不透明以下像素


def test_split_components_false_sheet_only(tmp_path: Path) -> None:
    """split_components=false → 仅 sheet.png，无 components.json（坑 9 假设）。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/ui_gen",
            json={"prompt": "x", "split_components": False},
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == ["sheet.png"]  # 仅聚合表
        assert state["ui_components"] is None  # 状态响应也不带分割数据
        final = json.loads(
            (tmp_path / "artifacts" / state["job_id"] / "final_outputs.json").read_text(
                "utf-8"
            )
        )
        assert set(final) == {"job_id", "created_at", "outputs"}  # ui_components 键省略


def test_final_outputs_backward_compatible_key_set(tmp_path: Path) -> None:
    """非 UI 线（generations）的 final_outputs.json 保持 P1 三键（向后兼容锁定）。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post(
            "/api/v1/generations", json={"prompt": "legacy line", "size": "128x128"}
        )
        assert resp.status_code == 202
        # generations 线的轮询端点
        job_id = resp.json()["job_id"]
        deadline = time.monotonic() + 10
        while True:
            body = client.get(f"/api/v1/generations/{job_id}").json()
            if body["status"] in ("succeeded", "failed"):
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert body["status"] == "succeeded"
        final = json.loads(
            (data_dir / "artifacts" / job_id / "final_outputs.json").read_text("utf-8")
        )
        assert set(final) == {"job_id", "created_at", "outputs"}  # 旧键集不变


# ---------- 失败分支与重试 ----------


def test_provider_4xx_fails_immediately_without_retry(tmp_path: Path) -> None:
    """4xx → failed 不可重试：Provider 恰被调用 1 次。"""
    provider = FakeProvider([ProviderRequestError("HTTP 401：bad key")], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post("/api/v1/ui_gen", json={"prompt": "x"})
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
    provider = FakeProvider([failure], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post("/api/v1/ui_gen", json={"prompt": "x"})
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
        _ui_sheet_png(),
    )
    with _client(tmp_path, provider) as (client, _):
        resp = client.post("/api/v1/ui_gen", json={"prompt": "x"})
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "failed"
        assert provider.calls == 3
        assert "502" in state["error"]


# ---------- 请求校验（422/404） ----------


def test_submit_validation_422(tmp_path: Path) -> None:
    """空 prompt / 超长 prompt / extra 字段 / 缺 prompt → 422。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        assert client.post("/api/v1/ui_gen", json={"prompt": ""}).status_code == 422
        assert (
            client.post("/api/v1/ui_gen", json={"prompt": "x" * 2001}).status_code == 422
        )
        assert (
            client.post(
                "/api/v1/ui_gen", json={"prompt": "x", "seed": 42}
            ).status_code
            == 422
        )
        assert client.post("/api/v1/ui_gen", json={}).status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quality", "ultra"),
        ("resolution", "4k"),
        ("aspect_ratio", "5:4"),
        ("background_color", "#ff0000"),
        ("background_color", "cccccc"),
    ],
)
def test_enum_out_of_range_422(tmp_path: Path, field: str, value: str) -> None:
    """枚举外取值（quality/resolution/aspect_ratio/background_color）→ 422。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        resp = client.post("/api/v1/ui_gen", json={"prompt": "x", field: value})
        assert resp.status_code == 422, f"{field}={value!r} 应被拒绝"


def test_get_unknown_job_404(tmp_path: Path) -> None:
    """未知 job_id（含穿越形态）→ 404。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        assert (
            client.get("/api/v1/ui_gen/ffffffffffffffffffffffffffffffff").status_code
            == 404
        )
        assert client.get("/api/v1/ui_gen/..%2F..%2Fetc").status_code == 404


# ---------- P4-L2：extract 路由（multipart 防线 + 端到端） ----------


def _extract_ui_png(
    matte: tuple[int, int, int],
    block: tuple[int, int, int, int],
    color: tuple[int, int, int],
    size: tuple[int, int] = (120, 120),
) -> bytes:
    """matte 底 + 单色块的不透明 UI 图（extract 参考图夹具）。"""
    x0, y0, x1, y1 = block
    img = Image.new("RGB", size, matte)
    px = img.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            px[x, y] = color
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _extract_files(
    images: list[bytes], payload: str = "{}"
) -> tuple[list[tuple[str, bytes, str]], dict[str, str]]:
    """multipart 请求字段：files[] 列表 + data 的 payload JSON 串（分开传，防畸形）。"""
    files = [( "files", (f"ref_{i}.png", data, "image/png")) for i, data in enumerate(images)]
    return files, {"payload": payload}


def test_extract_submit_poll_succeeded_full_contract(tmp_path: Path) -> None:
    """extract 端到端：202 → succeeded → sheet.png + components.json + 双坐标。

    provider.calls == 0 实证 extract 零 provider 调用（确定性管线）。
    """
    provider = FakeProvider([], _ui_sheet_png())
    img1 = _extract_ui_png((204, 204, 204), (10, 10, 60, 50), (200, 30, 30))
    img2 = _extract_ui_png((204, 204, 204), (20, 20, 70, 60), (30, 30, 200))
    with _client(tmp_path, provider) as (client, data_dir):
        files, data = _extract_files([img1, img2], '{"background_color": "#cccccc"}')
        resp = client.post("/api/v1/ui_gen/extract", files=files, data=data)
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]
        assert resp.json()["status"] == "pending"

        state = _poll_until_terminal(client, job_id)
        assert state["status"] == "succeeded"
        assert provider.calls == 0  # 零 provider 调用实证
        # params 契约：kind=ui_extract（对齐官方 canonical 值）
        assert state["params"]["kind"] == "ui_extract"
        assert state["params"]["background_color"] == "#cccccc"

        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == ["sheet.png", "components.json"]

        manifest = state["ui_components"]
        assert manifest is not None
        assert manifest["gate"]["passed"] is True
        assert manifest["gate"]["component_count"] == 2
        # 双坐标：components.json 落盘含 source_index/source_bbox
        comps_disk = json.loads(
            (data_dir / "artifacts" / job_id / "components.json").read_text("utf-8")
        )["components"]
        assert [c["source_index"] for c in comps_disk] == [0, 1]
        for c in comps_disk:
            assert set(c) == {"id", "label", "bbox", "area_px", "source_index", "source_bbox"}
            assert c["bbox"][2:] == c["source_bbox"][2:]

        # 磁盘 sheet：透明角落 + 组件本体
        sheet = _decode((data_dir / "artifacts" / job_id / "sheet.png").read_bytes())
        assert sheet.mode == "RGBA"
        px = sheet.load()
        assert px[0, 0][3] == 0


def test_extract_default_payload_auto_scan(tmp_path: Path) -> None:
    """payload 缺省 {} → background_color=None → 自动四角扫描（仍成功）。"""
    provider = FakeProvider([], _ui_sheet_png())
    img = _extract_ui_png((18, 52, 86), (10, 10, 50, 50), (200, 30, 30))
    with _client(tmp_path, provider) as (client, data_dir):
        files, data = _extract_files([img])
        resp = client.post("/api/v1/ui_gen/extract", files=files, data=data)
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        assert state["params"]["background_color"] is None
        sheet = _decode(
            (data_dir / "artifacts" / state["job_id"] / "sheet.png").read_bytes()
        )
        assert sheet.getextrema()[3][0] == 0  # 自动扫描去背生效


def test_extract_deterministic_two_jobs_identical_bytes(tmp_path: Path) -> None:
    """同输入两次提交 → 两个 job 的 sheet.png 字节级一致（确定性重排）。"""
    provider = FakeProvider([], _ui_sheet_png())
    images = [
        _extract_ui_png((204, 204, 204), (10, 10, 60, 50), (200, 30, 30)),
        _extract_ui_png((204, 204, 204), (20, 20, 70, 60), (30, 30, 200)),
    ]
    with _client(tmp_path, provider) as (client, data_dir):
        job_ids = []
        for _ in range(2):
            files, data = _extract_files(images, '{"background_color": "#cccccc"}')
            resp = client.post("/api/v1/ui_gen/extract", files=files, data=data)
            assert resp.status_code == 202
            job_ids.append(resp.json()["job_id"])
        for job_id in job_ids:
            state = _poll_until_terminal(client, job_id)
            assert state["status"] == "succeeded"
        sheet_a = (data_dir / "artifacts" / job_ids[0] / "sheet.png").read_bytes()
        sheet_b = (data_dir / "artifacts" / job_ids[1] / "sheet.png").read_bytes()
        assert sheet_a == sheet_b


def test_extract_zero_files_422(tmp_path: Path) -> None:
    """0 张参考图 → 422（官方 §3.2 硬门禁，先于 payload/文件内容校验）。"""
    provider = FakeProvider([], _ui_sheet_png())
    with _client(tmp_path, provider) as (client, _):
        # 完全不带 files 字段
        resp = client.post("/api/v1/ui_gen/extract", data={"payload": "{}"})
        assert resp.status_code == 422, resp.text
        assert "至少" in resp.json()["detail"]
        # 带 files= 但空列表
        resp = client.post("/api/v1/ui_gen/extract", files=[], data={"payload": "{}"})
        assert resp.status_code == 422, resp.text
        assert "至少" in resp.json()["detail"]


def test_extract_nine_files_422(tmp_path: Path) -> None:
    """9 张参考图 → 422（官方 >8 抛错；8 张边界合法）。"""
    provider = FakeProvider([], _ui_sheet_png())
    img = _extract_ui_png((204, 204, 204), (10, 10, 50, 50), (200, 30, 30))
    with _client(tmp_path, provider) as (client, _):
        files, data = _extract_files([img] * 9)
        assert (
            client.post("/api/v1/ui_gen/extract", files=files, data=data).status_code == 422
        )
        # 8 张边界：请求本身通过防线（任务 succeeded，全部提取出组件）
        files, data = _extract_files([img] * 8)
        resp = client.post("/api/v1/ui_gen/extract", files=files, data=data)
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"


def test_extract_bad_file_422(tmp_path: Path) -> None:
    """夹带非图像文件 → 422（魔数防线）。"""
    provider = FakeProvider([], _ui_sheet_png())
    good = _extract_ui_png((204, 204, 204), (10, 10, 50, 50), (200, 30, 30))
    with _client(tmp_path, provider) as (client, _):
        files = [
            ("files", ("good.png", good, "image/png")),
            ("files", ("evil.png", b"definitely-not-an-image", "image/png")),
        ]
        resp = client.post("/api/v1/ui_gen/extract", files=files, data={"payload": "{}"})
        assert resp.status_code == 422, resp.text


def test_extract_oversized_file_413(tmp_path: Path) -> None:
    """单文件超过 settings.max_upload_bytes → 413（tilesets 同款防线）。"""
    provider = FakeProvider([], _ui_sheet_png())
    settings = Settings(_env_file=None, data_dir=tmp_path, max_upload_bytes=64)
    store = FileSystemJobStore(settings.data_dir)
    app = create_app(settings=settings, factory=FakeFactory(provider), store=store)
    app.state.ui_gen_runner = UiGenJobRunner(store, FakeFactory(provider))
    with TestClient(app) as client:
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128  # 过魔数、超 64B 上限
        resp = client.post(
            "/api/v1/ui_gen/extract",
            files=[("files", ("big.png", big, "image/png"))],
            data={"payload": "{}"},
        )
        assert resp.status_code == 413, resp.text


def test_extract_payload_validation_422(tmp_path: Path) -> None:
    """payload extra 字段 / 非法 background_color / 非法 JSON → 422。"""
    provider = FakeProvider([], _ui_sheet_png())
    img = _extract_ui_png((204, 204, 204), (10, 10, 50, 50), (200, 30, 30))
    with _client(tmp_path, provider) as (client, _):
        for bad_payload in [
            '{"prompt": "extract 不收 prompt"}',  # extra 字段（UiGenParams 字段混入）
            '{"background_color": "#ff0000"}',  # 枚举外
            "not-json",
        ]:
            files, data = _extract_files([img], bad_payload)
            resp = client.post("/api/v1/ui_gen/extract", files=files, data=data)
            assert resp.status_code == 422, f"{bad_payload!r} 应被拒绝"


def test_extract_zero_components_job_failed(tmp_path: Path) -> None:
    """全部源图纯色零组件 → 管线 UiPipelineError → job failed（终态不悬空）。"""
    provider = FakeProvider([], _ui_sheet_png())
    img = Image.new("RGB", (80, 80), (204, 204, 204))  # 纯色无内容
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    with _client(tmp_path, provider) as (client, _):
        files, data = _extract_files([buf.getvalue()])
        resp = client.post("/api/v1/ui_gen/extract", files=files, data=data)
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "failed"
        assert "UiPipelineError" in state["error"]

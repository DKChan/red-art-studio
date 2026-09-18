"""animations API 端到端测试（P5-L1 帧序列打包线）。

形态：零 provider 确定性线 → 真 AnimationPackJobRunner → 真文件系统存储 →
TestClient 驱动真实路由，零真实外呼。provider.calls==0 实证零 provider 调用
（P4-L2 extract 同款断言模式）。必须用 `with TestClient(...)`（P2 实测纪律）。

夹具全部 Pillow 代码生成；断言全部数值取证（回读 n_frames/loop/网格像素），
不读图。
"""

import io
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from server.app.core.config import Settings
from server.app.core.storage import FileSystemJobStore
from server.app.jobs.executor import AnimationPackJobRunner
from server.app.main import create_app
from server.app.providers.base import (
    GeneratedImage,
    GenerationResult,
    sniff_image_format,
)


class CountingProvider:
    """计数 Provider：满足协议，仅用于断言动画线零 provider 调用。"""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_image(self, request: Any) -> GenerationResult:
        self.calls += 1
        return GenerationResult(images=[])

    async def aclose(self) -> None:
        return None


class CountingFactory:
    """固定返回同一 Provider 的工厂协议实现。"""

    def __init__(self, provider: CountingProvider) -> None:
        self._provider = provider

    @property
    def default_name(self) -> str:
        return "fake"

    def get(self, name: str | None = None) -> CountingProvider:
        return self._provider

    async def aclose(self) -> None:
        return None


# ---------- 夹具 ----------


def _frame(idx: int, size: tuple[int, int] = (12, 12)) -> bytes:
    """第 idx 帧的 PNG 字节（唯一纯色，位移渐变式序列取模可循环）。"""
    img = Image.new("RGBA", size, (idx * 40 % 256, 80, 160, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _frames(count: int, size: tuple[int, int] = (12, 12)) -> list[bytes]:
    return [_frame(i, size) for i in range(count)]


def _multipart(images: list[bytes], payload: str = "{}") -> dict[str, Any]:
    """multipart 请求体：files 字段列表 + payload JSON 串。"""
    files = [("files", (f"frame_{i:02d}.png", data, "image/png")) for i, data in enumerate(images)]
    return {"files": files, "data": {"payload": payload}}


@contextmanager
def _client(tmp_path: Path, provider: CountingProvider) -> Iterator[tuple[TestClient, Path]]:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    store = FileSystemJobStore(settings.data_dir)
    app = create_app(settings=settings, factory=CountingFactory(provider), store=store)
    app.state.anim_runner = AnimationPackJobRunner(store)
    with TestClient(app) as test_client:
        yield test_client, tmp_path


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/animations/{job_id}")
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


def test_webp_submit_poll_succeeded_full_contract(tmp_path: Path) -> None:
    """webp 端到端：202 → succeeded → animation.webp + anim_report 契约全字段。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post("/api/v1/animations", **_multipart(_frames(4)))
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        job_id = body["job_id"]

        state = _poll_until_terminal(client, job_id)
        assert state["status"] == "succeeded"
        assert state["error"] is None
        # params 契约：缺省值 + kind 落盘
        assert state["params"]["kind"] == "anim_pack"
        assert state["params"]["output_format"] == "webp"
        assert state["params"]["animation_type"] == "other"
        assert state["params"]["pixel"] is False
        assert state["params"]["alpha_mode"] is None
        assert state["params"]["color_count"] is None
        assert state["params"]["duration_ms"] == 125

        # 产物清单 + anim_report 回显
        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == ["animation.webp"]
        report = state["anim_report"]
        assert report is not None
        assert report["frame_count"] == 4
        assert report["frame_size"] == [12, 12]
        assert report["duration_ms"] == 125
        assert report["animation_type"] == "other"
        assert report["pixel"] is False
        assert report["alpha_mode"] == "soft"  # None 路由解析
        assert report["loop_report"]["first_last_max_step"] == 120
        assert report["loop_report"]["passed"] is False
        assert "columns" not in report  # webp 线不带网格字段（None 省略）
        assert "rows" not in report
        assert "sheet_meta" not in report

        # 磁盘 webp 回读：n_frames=4、loop=0、duration 一致
        webp = _decode((data_dir / "artifacts" / job_id / "animation.webp").read_bytes())
        assert getattr(webp, "n_frames", 1) == 4
        assert webp.info.get("loop") == 0
        durations = []
        for i in range(webp.n_frames):
            webp.seek(i)
            webp.load()
            durations.append(webp.info.get("duration"))
        assert durations == [125] * 4
        by_name = {o["filename"]: o for o in state["outputs"]}
        assert by_name["animation.webp"]["width"] == 12
        assert by_name["animation.webp"]["height"] == 12

        # final_outputs.json 键集：动画线四键（三键 + anim_report）
        final = json.loads(
            (data_dir / "artifacts" / job_id / "final_outputs.json").read_text("utf-8")
        )
        assert set(final) == {"job_id", "created_at", "outputs", "anim_report"}
        # artifacts 端点可取回
        art = client.get(f"/api/v1/artifacts/{job_id}/animation.webp")
        assert art.status_code == 200
        assert art.headers["content-type"].startswith("image/webp")


def test_gif_line_delivers_gif(tmp_path: Path) -> None:
    """gif 线：animation.gif 产物，回读 n_frames/loop=0。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post(
            "/api/v1/animations", **_multipart(_frames(2), '{"output_format": "gif"}')
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        assert [o["filename"] for o in state["outputs"]] == ["animation.gif"]
        gif = _decode((data_dir / "artifacts" / state["job_id"] / "animation.gif").read_bytes())
        assert getattr(gif, "n_frames", 1) == 2
        assert gif.info.get("loop") == 0
        assert gif.format == "GIF"


def test_spritesheet_line_full_contract(tmp_path: Path) -> None:
    """spritesheet 线：sheet.png + spritesheet.json + 网格元数据（坑 7 契约）。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, data_dir):
        resp = client.post(
            "/api/v1/animations",
            **_multipart(
                _frames(4, (10, 8)),
                '{"output_format": "spritesheet", "animation_type": "walk", "duration_ms": 100}',
            ),
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        filenames = [o["filename"] for o in state["outputs"]]
        assert filenames == ["sheet.png", "spritesheet.json"]

        report = state["anim_report"]
        assert report["columns"] == 2
        assert report["rows"] == 2
        assert report["animation_type"] == "walk"
        meta = report["sheet_meta"]
        assert meta["frame_size"] == [10, 8]
        assert meta["columns"] == 2
        assert meta["rows"] == 2
        assert meta["frame_durations"] == [100, 100, 100, 100]
        assert meta["loop"] is True
        assert meta["animation_type"] == "walk"

        # 磁盘 sheet 网格逐格 + 末行空位透明
        sheet = _decode((data_dir / "artifacts" / state["job_id"] / "sheet.png").read_bytes())
        assert sheet.size == (20, 16)
        px = sheet.load()
        for idx, (ox, oy) in enumerate([(0, 0), (10, 0), (0, 8), (10, 8)]):
            expected = (idx * 40 % 256, 80, 160, 255)
            assert px[ox + 2, oy + 3] == expected, f"帧 {idx} 逐格断言失败"

        # spritesheet.json 磁盘契约（引擎切图元数据）
        meta_disk = json.loads(
            (data_dir / "artifacts" / state["job_id"] / "spritesheet.json").read_text("utf-8")
        )
        assert meta_disk == meta

        # artifacts 端点 spritesheet.json 的 Content-Type
        art = client.get(f"/api/v1/artifacts/{state['job_id']}/spritesheet.json")
        assert art.status_code == 200
        assert art.headers["content-type"].startswith("application/json")


def test_pixel_line_end_to_end(tmp_path: Path) -> None:
    """pixel=true 端到端：alpha_mode 回显 sharp、调色板统一生效（异色被统一到首帧）。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, data_dir):
        # 首帧红、末帧绿（全可见）：统一后 RGB 应一致 → loop_report 跳变 0
        first = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
        last = Image.new("RGBA", (8, 8), (0, 255, 0, 255))
        images = []
        for img in (first, last):
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            images.append(buf.getvalue())
        resp = client.post(
            "/api/v1/animations",
            **_multipart(images, '{"pixel": true, "animation_type": "idle"}'),
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "succeeded"
        report = state["anim_report"]
        assert report["pixel"] is True
        assert report["alpha_mode"] == "sharp"
        assert report["loop_report"]["first_last_max_step"] == 0
        assert report["loop_report"]["passed"] is True


def test_deterministic_two_jobs_identical_bytes(tmp_path: Path) -> None:
    """同输入两次提交 → sheet.png 与 spritesheet.json 字节级一致（确定性锁定）。"""
    provider = CountingProvider()
    images = _frames(4, (10, 8))
    with _client(tmp_path, provider) as (client, data_dir):
        job_ids = []
        for _ in range(2):
            resp = client.post(
                "/api/v1/animations",
                **_multipart(images, '{"output_format": "spritesheet"}'),
            )
            assert resp.status_code == 202
            job_ids.append(resp.json()["job_id"])
        for job_id in job_ids:
            state = _poll_until_terminal(client, job_id)
            assert state["status"] == "succeeded"
        for name in ("sheet.png", "spritesheet.json"):
            a = (data_dir / "artifacts" / job_ids[0] / name).read_bytes()
            b = (data_dir / "artifacts" / job_ids[1] / name).read_bytes()
            assert a == b, f"{name} 应字节级一致"


def test_final_outputs_backward_compatible_key_set(tmp_path: Path) -> None:
    """P1 形态 final_outputs.json（三键）经新模型读回不受 anim_report 影响。

    旧线（无 anim_report 键）反序列化后 anim_report=None；序列化回去键集不变
    （None 序列化省略）。旧线契约形态由 test_ui_gen/test_generations 端到端
    锁定，本测试锁定**磁盘读回兼容**（anim_report 字段加入后的向后兼容面）。
    """
    from server.app.jobs.models import FinalOutputs, OutputFile, utc_now_iso

    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, data_dir):
        legacy_job = "0" * 32
        legacy = FinalOutputs(
            job_id=legacy_job,
            created_at=utc_now_iso(),
            outputs=[
                OutputFile(filename="000.png", width=8, height=8, format="png"),
            ],
        )
        store = FileSystemJobStore(data_dir)
        store.save_final_outputs(legacy)
        path = data_dir / "artifacts" / legacy_job / "final_outputs.json"
        disk = json.loads(path.read_text("utf-8"))
        assert set(disk) == {"job_id", "created_at", "outputs"}
        # 读回：旧文件经新模型反序列化，anim_report 缺省 None 不炸
        loaded = FinalOutputs.model_validate_json(path.read_text("utf-8"))
        assert loaded.anim_report is None
        # 动画线自己的四键形态由 test_webp_submit_poll_succeeded_full_contract 锁定


# ---------- 帧数门禁与防线 ----------


def test_frame_count_gate_422(tmp_path: Path) -> None:
    """0/1/17 张与奇数张 422（门禁在读文件内容前）；2/16 边界通过。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, _):
        # 0 张：无 files 字段 / 空列表两种形态
        r0 = client.post("/api/v1/animations", data={"payload": "{}"})
        assert r0.status_code == 422
        assert "至少" in r0.json()["detail"]
        r0b = client.post("/api/v1/animations", **_multipart([]))
        assert r0b.status_code == 422
        # 1 张
        r1 = client.post("/api/v1/animations", **_multipart(_frames(1)))
        assert r1.status_code == 422
        # 17 张（超上限）
        r17 = client.post("/api/v1/animations", **_multipart(_frames(17)))
        assert r17.status_code == 422
        assert "最多" in r17.json()["detail"]
        # 3 张（奇数）
        r3 = client.post("/api/v1/animations", **_multipart(_frames(3)))
        assert r3.status_code == 422
        assert "偶数" in r3.json()["detail"]
        # 边界：2 与 16 通过防线（任务受理，job succeeded）
        for count in (2, 16):
            resp = client.post("/api/v1/animations", **_multipart(_frames(count)))
            assert resp.status_code == 202
            state = _poll_until_terminal(client, resp.json()["job_id"])
            assert state["status"] == "succeeded"


def test_mixed_size_frames_job_failed(tmp_path: Path) -> None:
    """异尺寸帧通过路由防线、被管线拒绝 → job failed（终态不悬空）。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, _):
        # 4 张合法数量（过偶数门禁），其中 1 张异尺寸 → 管线层拒绝
        images = _frames(3) + [_frame(0, (24, 24))]
        resp = client.post("/api/v1/animations", **_multipart(images))
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "failed"
        assert "AnimPipelineError" in state["error"]


def test_pixel_canvas_257_job_failed_256_ok(tmp_path: Path) -> None:
    """pixel=true 画布 257 → 管线拒绝 failed；256 → succeeded。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, _):
        big = Image.new("RGBA", (257, 64), (0, 0, 0, 255))
        buf = io.BytesIO()
        big.save(buf, format="PNG")
        resp = client.post(
            "/api/v1/animations", **_multipart([buf.getvalue()] * 2, '{"pixel": true}')
        )
        assert resp.status_code == 202
        state = _poll_until_terminal(client, resp.json()["job_id"])
        assert state["status"] == "failed"
        assert "256" in state["error"]

        ok = Image.new("RGBA", (256, 64), (0, 0, 0, 255))
        buf_ok = io.BytesIO()
        ok.save(buf_ok, format="PNG")
        resp_ok = client.post(
            "/api/v1/animations", **_multipart([buf_ok.getvalue()] * 2, '{"pixel": true}')
        )
        assert resp_ok.status_code == 202
        state_ok = _poll_until_terminal(client, resp_ok.json()["job_id"])
        assert state_ok["status"] == "succeeded"


def test_color_count_without_pixel_422(tmp_path: Path) -> None:
    """color_count 无 pixel 携带 → 422（交叉校验）；pixel=true 合法。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/animations", **_multipart(_frames(2), '{"color_count": 16}')
        )
        assert resp.status_code == 422
        resp_bad = client.post(
            "/api/v1/animations", **_multipart(_frames(2), '{"color_count": 1, "pixel": true}')
        )
        assert resp_bad.status_code == 422  # 取值域 2-64
        resp_ok = client.post(
            "/api/v1/animations", **_multipart(_frames(2), '{"color_count": 16, "pixel": true}')
        )
        assert resp_ok.status_code == 202


def test_payload_extra_field_422(tmp_path: Path) -> None:
    """payload extra 字段 422（extra=forbid，杜绝静默吞参）。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, _):
        resp = client.post(
            "/api/v1/animations", **_multipart(_frames(2), '{"prompt": "动画线不收 prompt"}')
        )
        assert resp.status_code == 422
        resp_bad = client.post("/api/v1/animations", **_multipart(_frames(2), "not-json"))
        assert resp_bad.status_code == 422


def test_bad_magic_422(tmp_path: Path) -> None:
    """坏文件魔数 → 422（_read_texture 防线）。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, _):
        files = [
            ("files", ("good.png", _frame(0), "image/png")),
            ("files", ("evil.png", b"definitely-not-an-image", "image/png")),
        ]
        resp = client.post("/api/v1/animations", files=files, data={"payload": "{}"})
        assert resp.status_code == 422


def test_oversized_413(tmp_path: Path) -> None:
    """单文件超过上限 → 413。"""
    provider = CountingProvider()
    settings = Settings(_env_file=None, data_dir=tmp_path, max_upload_bytes=64)
    store = FileSystemJobStore(settings.data_dir)
    app = create_app(settings=settings, factory=CountingFactory(provider), store=store)
    app.state.anim_runner = AnimationPackJobRunner(store)
    with TestClient(app) as client:
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128  # 过魔数、超 64B
        resp = client.post(
            "/api/v1/animations",
            files=[("files", ("big.png", big, "image/png"))] * 2,
            data={"payload": "{}"},
        )
        assert resp.status_code == 413, resp.text


def test_animated_input_422(tmp_path: Path) -> None:
    """动画格式输入（多帧 GIF/WebP）→ 422（静默丢信息防线）。

    双层防线分野：动画 GIF 魔数（GIF87a/89a）本就在白名单外 → 魔数层 422；
    动画 WebP 魔数合法（RIFF/WEBP）→ 深入到 n_frames 检测层 422。两层都是
    "动画输入不收"语义，报文不同。
    """
    provider = CountingProvider()
    frames = [_frame(i, (8, 8)) for i in range(2)]
    imgs = [_decode(f) for f in frames]
    gif_buf = io.BytesIO()
    imgs[0].save(gif_buf, format="GIF", save_all=True, append_images=[imgs[1]], loop=0)
    webp_buf = io.BytesIO()
    imgs[0].save(webp_buf, format="WEBP", save_all=True, append_images=[imgs[1]], loop=0)
    with _client(tmp_path, provider) as (client, _):
        # 动画 GIF：魔数层拒绝
        files = [
            ("files", ("good.png", _frame(0), "image/png")),
            ("files", ("animated.gif", gif_buf.getvalue(), "application/octet-stream")),
        ]
        resp = client.post("/api/v1/animations", files=files, data={"payload": "{}"})
        assert resp.status_code == 422, resp.text
        assert "魔数" in resp.json()["detail"]
        # 动画 WebP：动画输入检测层拒绝（n_frames>1）
        files = [
            ("files", ("good.png", _frame(0), "image/png")),
            ("files", ("animated.webp", webp_buf.getvalue(), "image/webp")),
        ]
        resp = client.post("/api/v1/animations", files=files, data={"payload": "{}"})
        assert resp.status_code == 422, resp.text
        assert "动画格式输入" in resp.json()["detail"]


def test_unknown_job_404(tmp_path: Path) -> None:
    """未知 job_id（含穿越形态）→ 404。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, _):
        assert (
            client.get("/api/v1/animations/ffffffffffffffffffffffffffffffff").status_code == 404
        )
        assert client.get("/api/v1/animations/..%2F..%2Fetc").status_code == 404


def test_zero_provider_calls(tmp_path: Path) -> None:
    """provider.calls==0 实证：动画打包线全程零 provider 调用。"""
    provider = CountingProvider()
    with _client(tmp_path, provider) as (client, _):
        for payload in (
            '{"output_format": "webp"}',
            '{"output_format": "gif"}',
            '{"output_format": "spritesheet"}',
        ):
            resp = client.post("/api/v1/animations", **_multipart(_frames(2), payload))
            assert resp.status_code == 202
            state = _poll_until_terminal(client, resp.json()["job_id"])
            assert state["status"] == "succeeded"
        assert provider.calls == 0


def test_sniff_format_helper_used() -> None:
    """嗅探函数可导入性守卫（防线依赖的既有模块不被意外移除）。"""
    assert callable(sniff_image_format)
    assert callable(GeneratedImage)

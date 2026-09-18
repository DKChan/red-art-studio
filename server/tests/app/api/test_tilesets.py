"""tilesets API 端到端测试（P3-L1 验收 1：路由 4xx 分支 + 全链路合成）。

形态与 test_image_edits.py 同款：真处理器（确定性纯 CPU）+ 真 JobRunner +
真文件系统存储 + TestClient 驱动真实路由，零 mock 外呼。
必须用 `with TestClient(...)`：上下文持常驻 portal/事件循环并跑 lifespan
（P2 实测：裸实例每请求独立 loop，后台 to_thread 永卡 running）。
"""

import io
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from server.app.core.config import Settings
from server.app.core.map_layout import dual_grid_atlas_cell
from server.app.core.storage import FileSystemJobStore
from server.app.jobs.executor import TilesetJobRunner
from server.app.main import create_app

_A_RGB = (16, 32, 48)
_B_RGB = (200, 40, 240)
_CELL = 64


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _texture(rgb: tuple[int, int, int]) -> bytes:
    return _png(Image.new("RGBA", (_CELL, _CELL), (*rgb, 255)))


@contextmanager
def _client(tmp_path: Path, **settings_overrides: object) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, data_dir=tmp_path, **settings_overrides)  # type: ignore[arg-type]
    store = FileSystemJobStore(settings.data_dir)
    app = create_app(settings=settings, store=store)
    app.state.tileset_runner = TilesetJobRunner(store)
    with TestClient(app) as test_client:
        yield test_client


def _submit(
    client: TestClient,
    payload: dict,
    background: bytes | None = None,
    foreground: bytes | None = None,
    bg_content_type: str = "image/png",
    fg_content_type: str = "image/png",
):
    return client.post(
        "/api/v1/tilesets",
        files={
            # 显式 is None 判断：b"" 是 falsy，`or` 回落会把空文件测试静默换成真纹理
            "background": (
                "bg.png",
                background if background is not None else _texture(_A_RGB),
                bg_content_type,
            ),
            "foreground": (
                "fg.png",
                foreground if foreground is not None else _texture(_B_RGB),
                fg_content_type,
            ),
        },
        data={"payload": json.dumps(payload)},
    )


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/tilesets/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"任务在 {timeout}s 内未到达终态")


def _cell_pixels(img: Image.Image, key: int) -> set:
    col, row = dual_grid_atlas_cell(key)
    return set(
        img.crop((col * _CELL, row * _CELL, (col + 1) * _CELL, (row + 1) * _CELL)).getdata()
    )


def test_submit_dual_success_end_to_end(tmp_path: Path) -> None:
    """dual 模式全链路：嵌套参数生效 → succeeded → 256×256 图集 + 格位像素断言。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"terrain_mode": "dual", "seed": 5, "feather_width": 1.0})
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        final = _poll_until_terminal(client, job_id)
        assert final["status"] == "succeeded", final.get("error")
        assert final["params"] == {
            "kind": "tileset",
            "terrain_mode": "dual",
            "seed": 5,
            "feather_width": 1.0,
        }
        out = final["outputs"][0]
        assert out["filename"] == "000.png"
        assert out["width"] == 256 and out["height"] == 256
        assert out["format"] == "png"

        artifact = client.get(f"/api/v1/artifacts/{job_id}/000.png")
        assert artifact.status_code == 200
        img = Image.open(io.BytesIO(artifact.content))
        img.load()
        img = img.convert("RGBA")
        assert img.size == (256, 256)
        # 格位契约（官方表）：key0 → (0,3) 纯 A；key15 → (2,1) 纯 B
        assert _cell_pixels(img, 0) == {(*_A_RGB, 255)}
        assert _cell_pixels(img, 15) == {(*_B_RGB, 255)}


def test_submit_foreground_mode_end_to_end(tmp_path: Path) -> None:
    """foreground 镂空模式全链路：key0 全透明。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"terrain_mode": "foreground"})
        assert resp.status_code == 202, resp.text
        final = _poll_until_terminal(client, resp.json()["job_id"])
        assert final["status"] == "succeeded", final.get("error")
        artifact = client.get(
            f"/api/v1/artifacts/{resp.json()['job_id']}/000.png"
        )
        img = Image.open(io.BytesIO(artifact.content))
        img.load()
        alphas = set(
            p[3]
            for p in img.convert("RGBA").crop((0, 192, 64, 256)).getdata()  # key0 → (0,3)
        )
        assert alphas == {0}


def test_submit_unknown_terrain_mode_422(tmp_path: Path) -> None:
    """terrain_mode 取值域外 → 422（pydantic Literal 校验）。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"terrain_mode": "interleaved"})
        assert resp.status_code == 422, resp.text


def test_submit_unknown_param_rejected_422(tmp_path: Path) -> None:
    """未知参数显式 422（extra=forbid），不许静默吞掉（P2 教训回归防线）。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"terrain_mode": "dual", "bogus": 1})
        assert resp.status_code == 422, resp.text


def test_submit_out_of_range_seed_422(tmp_path: Path) -> None:
    """seed 越界 → 422（pydantic ge/le）。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"terrain_mode": "dual", "seed": -1})
        assert resp.status_code == 422, resp.text
        resp = _submit(client, {"terrain_mode": "dual", "seed": 2**32})
        assert resp.status_code == 422, resp.text


def test_submit_out_of_range_feather_422(tmp_path: Path) -> None:
    """feather_width 越界 → 422。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"terrain_mode": "dual", "feather_width": 0.1})
        assert resp.status_code == 422, resp.text
        resp = _submit(client, {"terrain_mode": "dual", "feather_width": 100})
        assert resp.status_code == 422, resp.text


def test_submit_invalid_payload_json_422(tmp_path: Path) -> None:
    """payload 非 JSON → 422。"""
    with _client(tmp_path) as client:
        resp = client.post(
            "/api/v1/tilesets",
            files={
                "background": ("bg.png", _texture(_A_RGB), "image/png"),
                "foreground": ("fg.png", _texture(_B_RGB), "image/png"),
            },
            data={"payload": "not json at all"},
        )
        assert resp.status_code == 422, resp.text


def test_submit_wrong_texture_size_fails_job(tmp_path: Path) -> None:
    """非 64×64 纹理通过上传防线（是合法 PNG）但合成强校验拒绝 → job failed。

    64×64 强校验在纯函数层（路由只查魔数/大小），故走异步 failed 分支而非 422。
    """
    with _client(tmp_path) as client:
        resp = _submit(
            client,
            {"terrain_mode": "dual"},
            background=_png(Image.new("RGBA", (128, 128), (0, 0, 0, 255))),
        )
        assert resp.status_code == 202, resp.text
        final = _poll_until_terminal(client, resp.json()["job_id"])
        assert final["status"] == "failed"
        assert "64×64" in final["error"]


def test_submit_bad_magic_422(tmp_path: Path) -> None:
    """任一纹理魔数不在白名单 → 422。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"terrain_mode": "dual"}, foreground=b"GIF89a" + b"0" * 32)
        assert resp.status_code == 422, resp.text


def test_submit_oversized_file_413(tmp_path: Path) -> None:
    """纹理超过 settings.max_upload_bytes → 413。"""
    with _client(tmp_path, max_upload_bytes=64) as client:
        resp = _submit(
            client, {"terrain_mode": "dual"}, background=b"\x89PNG\r\n\x1a\n" + b"0" * 128
        )
        assert resp.status_code == 413, resp.text


def test_submit_empty_file_422(tmp_path: Path) -> None:
    """空纹理文件 → 422。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"terrain_mode": "dual"}, foreground=b"")
        assert resp.status_code == 422, resp.text


def test_submit_disallowed_content_type_422(tmp_path: Path) -> None:
    """Content-Type 白名单外 → 422（第一道拦截）。"""
    with _client(tmp_path) as client:
        resp = _submit(
            client,
            {"terrain_mode": "dual"},
            bg_content_type="text/plain",
        )
        assert resp.status_code == 422, resp.text


def test_get_unknown_job_404(tmp_path: Path) -> None:
    """状态查询 job 不存在 → 404。"""
    with _client(tmp_path) as client:
        resp = client.get("/api/v1/tilesets/" + "a" * 32)
        assert resp.status_code == 404, resp.text

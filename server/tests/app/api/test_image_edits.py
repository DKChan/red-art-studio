"""image_edits API 端到端测试（P2 Loop 验收：路由层 4xx 分支 + 嵌套 payload 契约）。

形态：真处理器（纯 CPU 秒级）+ 真 JobRunner + 真文件系统存储 + TestClient 驱动
真实路由，零 mock 外呼。覆盖任务书验收 1：成功路径（嵌套 payload 形态回显）、
非法 operation / 文件魔数 / 超大文件 / 空文件 4xx、404。
嵌套 {"operation","params"} 形态曾是漏网 bug（pydantic 静默吞 params），
此处以"pixel_size 参数真实生效"锁定归一行为。

必须用 `with TestClient(...)`：上下文持常驻 portal/事件循环并跑 lifespan；
裸 TestClient 每请求起独立 loop、请求结束即销毁，后台 to_thread 任务永远
卡在 running（实 watched race：单跑过、全套挂）。
"""

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from server.app.core.config import Settings
from server.app.core.storage import FileSystemJobStore
from server.app.jobs.executor import ImageEditJobRunner
from server.app.main import create_app


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@contextmanager
def _client(tmp_path: Path, **settings_overrides: object) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, data_dir=tmp_path, **settings_overrides)  # type: ignore[arg-type]
    store = FileSystemJobStore(settings.data_dir)
    app = create_app(settings=settings, store=store)
    app.state.image_edit_runner = ImageEditJobRunner(store)
    with TestClient(app) as test_client:
        yield test_client


def _submit(client: TestClient, payload: dict, data: bytes, content_type: str = "image/png"):
    return client.post(
        "/api/v1/image-edits",
        files={"file": ("in.png", data, content_type)},
        data={"payload": json.dumps(payload)},
    )


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/image-edits/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"任务在 {timeout}s 内未到达终态")


def test_submit_nested_payload_success_and_params_effective(tmp_path: Path) -> None:
    """嵌套 wire 形态提交成功，params 内参数真实生效（pixel_size=8 → 输出块 8px）。"""
    with _client(tmp_path) as client:
        img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        resp = _submit(
            client,
            {"operation": "pixelate", "params": {"pixel_size": 8}},
            _png(img),
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        final = _poll_until_terminal(client, job_id)
        assert final["status"] == "succeeded"
        # params 回显嵌套形态归一后的扁平契约（API 状态模型与 generations 同构）
        assert final["params"] == {
            "kind": "image_edit",
            "operation": "pixelate",
            "pixel_size": 8,
            "source_background_color": None,
            "tolerance": None,
            "direction": None,
        }
        # 输出图 32//8*8=32 且为 PNG；块一致性交给 processors 单测，这里只验尺寸契约
        assert final["outputs"][0]["filename"] == "000.png"
        assert final["outputs"][0]["width"] == 32 and final["outputs"][0]["height"] == 32

        artifact = client.get(f"/api/v1/artifacts/{job_id}/000.png")
        assert artifact.status_code == 200


def test_submit_unknown_operation_422(tmp_path: Path) -> None:
    """operation 取值域外 → 422（pydantic Literal 校验）。"""
    with _client(tmp_path) as client:
        resp = _submit(
            client,
            {"operation": "teleport", "params": {}},
            _png(Image.new("RGBA", (8, 8))),
        )
        assert resp.status_code == 422, resp.text


def test_submit_unknown_param_rejected_not_swallowed(tmp_path: Path) -> None:
    """未知参数显式 422（extra=forbid），不许静默吞掉（嵌套形态 bug 的回归防线）。"""
    with _client(tmp_path) as client:
        resp = _submit(
            client,
            {"operation": "pixelate", "params": {"pixel_size": 8, "bogus": 1}},
            _png(Image.new("RGBA", (8, 8))),
        )
        assert resp.status_code == 422, resp.text


def test_submit_bad_magic_422(tmp_path: Path) -> None:
    """魔数不属于 png/jpeg/webp 白名单 → 422。"""
    with _client(tmp_path) as client:
        resp = _submit(
            client,
            {"operation": "pixelate"},
            b"GIF89a" + b"0" * 32,
        )
        assert resp.status_code == 422, resp.text


def test_submit_oversized_file_413(tmp_path: Path) -> None:
    """超过 settings.max_upload_bytes 上限 → 413。"""
    with _client(tmp_path, max_upload_bytes=64) as client:
        resp = _submit(client, {"operation": "pixelate"}, b"\x89PNG\r\n\x1a\n" + b"0" * 128)
        assert resp.status_code == 413, resp.text


def test_submit_empty_file_422(tmp_path: Path) -> None:
    """空文件 → 422。"""
    with _client(tmp_path) as client:
        resp = _submit(client, {"operation": "pixelate"}, b"")
        assert resp.status_code == 422, resp.text


def test_submit_disallowed_content_type_422(tmp_path: Path) -> None:
    """Content-Type 白名单外 → 422（第一道拦截，魔数校验前的廉价防线）。"""
    with _client(tmp_path) as client:
        resp = client.post(
            "/api/v1/image-edits",
            files={"file": ("in.txt", b"\x89PNG\r\n\x1a\n" + b"0" * 16, "text/plain")},
            data={"payload": json.dumps({"operation": "pixelate"})},
        )
        assert resp.status_code == 422, resp.text


def test_submit_invalid_payload_json_422(tmp_path: Path) -> None:
    """payload 非 JSON / 字段越界（pixel_size=3）→ 422。"""
    with _client(tmp_path) as client:
        resp = client.post(
            "/api/v1/image-edits",
            files={"file": ("in.png", _png(Image.new("RGBA", (8, 8))), "image/png")},
            data={"payload": "not json at all"},
        )
        assert resp.status_code == 422, resp.text

        resp = _submit(
            client,
            {"operation": "pixelate", "params": {"pixel_size": 3}},
            _png(Image.new("RGBA", (8, 8))),
        )
        assert resp.status_code == 422, resp.text


def test_remove_background_end_to_end(tmp_path: Path) -> None:
    """remove_background 全链路：嵌套 payload → succeeded → 产物角点透明。"""
    with _client(tmp_path) as client:
        img = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
        resp = _submit(
            client,
            {
                "operation": "remove_background",
                "params": {"source_background_color": "#ff0000"},
            },
            _png(img),
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]
        final = _poll_until_terminal(client, job_id)
        assert final["status"] == "succeeded"
        artifact = client.get(f"/api/v1/artifacts/{job_id}/000.png")
        out = Image.open(io.BytesIO(artifact.content))
        out.load()
        assert out.getpixel((0, 0))[3] == 0


def test_get_unknown_job_404(tmp_path: Path) -> None:
    """状态查询 job 不存在 → 404。"""
    with _client(tmp_path) as client:
        resp = client.get("/api/v1/image-edits/" + "a" * 32)
        assert resp.status_code == 404, resp.text

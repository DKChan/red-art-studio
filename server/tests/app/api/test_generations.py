"""generations API 端到端测试（ROADMAP §P1 L3 验收）。

形态：httpx.MockTransport 假冒推理后端 → 真 Provider → 真 JobRunner →
真文件系统存储 → ASGITransport/TestClient 驱动真实路由。
submit → 轮询至终态 → 校验产物文件与 final_outputs.json schema；
另覆盖 failed 分支、404、422。零真实外呼。
"""

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server.app.core.config import Settings
from server.app.core.storage import FileSystemJobStore
from server.app.jobs.executor import GenerationJobRunner
from server.app.main import create_app
from server.app.providers.factory import SettingsProviderFactory
from server.app.providers.openai_compat import OpenAICompatProvider

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-body-0123456789"


def _b64(payload: bytes) -> str:
    import base64

    return base64.b64encode(payload).decode("ascii")


def _fake_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        provider="openai_compat",
        provider_base_url="https://api.example.com/v1",
        provider_api_key="sk-test",
        provider_model="gpt-image-1",
        provider_timeout_seconds=5.0,
        data_dir=tmp_path,
    )


def _app_with_mock_backend(
    tmp_path: Path, backend: httpx.MockTransport
) -> TestClient:
    """组装整条真实链路，仅推理后端 HTTP 层换成 MockTransport。"""
    settings = _fake_settings(tmp_path)
    provider = OpenAICompatProvider(
        base_url=settings.provider_base_url,
        api_key=settings.provider_api_key,
        model=settings.provider_model,
        timeout_seconds=settings.provider_timeout_seconds,
        client=httpx.AsyncClient(
            base_url=settings.provider_base_url, transport=backend
        ),
    )
    factory = SettingsProviderFactory(settings, {"openai_compat": provider})
    store = FileSystemJobStore(settings.data_dir)
    runner = GenerationJobRunner(store)
    app = create_app(settings=settings, factory=factory, store=store)
    app.state.runner = runner  # 用带注入 Provider 的 runner 覆盖 lifespan 默认装配
    return TestClient(app)


def _success_backend() -> httpx.MockTransport:
    """假冒 OpenAI images 端点：返回 1 张 b64 编码 PNG。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/images/generations"
        return httpx.Response(200, json={"data": [{"b64_json": _b64(PNG_BYTES)}]})

    return httpx.MockTransport(handler)


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    """轮询任务直到离开 pending/running（用 async sleep 让后台任务真正推进）。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/generations/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"任务在 {timeout}s 内未到达终态")


def test_submit_poll_succeeded_with_artifacts(tmp_path: Path) -> None:
    """L3 核心验收：submit → 轮询至 succeeded → 产物文件 + final_outputs.json 校验。"""
    client = _app_with_mock_backend(tmp_path, _success_backend())

    # 默认参数：size=1024x1024, n=1
    resp = client.post("/api/v1/generations", json={"prompt": "a pixel art cat"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    job_id = body["job_id"]
    assert len(job_id) == 32

    final_state = _poll_until_terminal(client, job_id)
    assert final_state["status"] == "succeeded"
    assert final_state["params"]["prompt"] == "a pixel art cat"
    assert final_state["params"]["size"] == "1024x1024"
    assert final_state["params"]["n"] == 1
    assert final_state["params"]["provider"] == "openai_compat"
    assert final_state["error"] is None

    # 产物清单进入状态响应
    outputs = final_state["outputs"]
    assert len(outputs) == 1
    assert outputs[0]["filename"] == "000.png"
    assert outputs[0]["format"] == "png"

    # 磁盘上的图像文件字节 = Provider 返回的字节
    artifact = tmp_path / "artifacts" / job_id / "000.png"
    assert artifact.is_file()
    assert artifact.read_bytes() == PNG_BYTES

    # final_outputs.json：文件存在、schema 与锁定契约一致、与图像文件对应
    final_path = tmp_path / "artifacts" / job_id / "final_outputs.json"
    assert final_path.is_file()
    final = json.loads(final_path.read_text(encoding="utf-8"))
    assert set(final) == {"job_id", "created_at", "outputs"}
    assert final["job_id"] == job_id
    assert len(final["outputs"]) == 1
    # PNG_BYTES 的假头解析不出真实尺寸，宽高按契约回落 0，格式仍正确
    assert final["outputs"][0] == {
        "filename": "000.png",
        "width": 0,
        "height": 0,
        "format": "png",
    }

    # job.json 契约：终态落盘
    job_record = json.loads((tmp_path / "jobs" / job_id / "job.json").read_text("utf-8"))
    assert job_record["status"] == "succeeded"
    assert job_record["started_at"] is not None
    assert job_record["finished_at"] is not None

    client.close()


def test_explicit_params_forwarded(tmp_path: Path) -> None:
    """显式 size/n/provider 传入并被转发给后端。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.update(payload)
        return httpx.Response(
            200,
            json={"data": [{"b64_json": _b64(PNG_BYTES)}] * payload["n"]},
        )

    client = _app_with_mock_backend(tmp_path, httpx.MockTransport(handler))
    resp = client.post(
        "/api/v1/generations",
        json={"prompt": "a tile", "size": "512x512", "n": 2, "provider": "openai_compat"},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    state = _poll_until_terminal(client, job_id)
    assert state["status"] == "succeeded"
    assert captured["size"] == "512x512"
    assert captured["n"] == 2
    assert len(state["outputs"]) == 2
    assert [o["filename"] for o in state["outputs"]] == ["000.png", "001.png"]
    client.close()


def test_failed_branch_records_error(tmp_path: Path) -> None:
    """failed 分支：后端 4xx → 任务 failed，error 落盘并回传。"""
    client = _app_with_mock_backend(
        tmp_path,
        httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": {"message": "bad key"}})
        ),
    )
    resp = client.post("/api/v1/generations", json={"prompt": "x"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    state = _poll_until_terminal(client, job_id)
    assert state["status"] == "failed"
    assert "401" in state["error"]
    assert state["outputs"] == []
    assert (tmp_path / "artifacts" / job_id).exists() is False

    # failed 任务的 job.json 也落盘
    job_record = json.loads((tmp_path / "jobs" / job_id / "job.json").read_text("utf-8"))
    assert job_record["status"] == "failed"
    assert job_record["error"] is not None
    client.close()


def test_get_unknown_job_returns_404(tmp_path: Path) -> None:
    """不存在的 job_id → 404（含非法格式的 id，同样 404 而非 500）。"""
    client = _app_with_mock_backend(tmp_path, _success_backend())
    resp = client.get("/api/v1/generations/ffffffffffffffffffffffffffffffff")
    assert resp.status_code == 404
    # 路径注入尝试也必须 404，不能 500 或泄漏目录结构
    resp = client.get("/api/v1/generations/..%2F..%2Fetc")
    assert resp.status_code == 404
    client.close()


def test_submit_validation_errors_422(tmp_path: Path) -> None:
    """参数校验失败 → pydantic/FastAPI 自动 422。"""
    client = _app_with_mock_backend(tmp_path, _success_backend())

    # prompt 缺失 / 空串
    assert client.post("/api/v1/generations", json={}).status_code == 422
    assert client.post("/api/v1/generations", json={"prompt": ""}).status_code == 422
    # size 非法格式
    assert client.post(
        "/api/v1/generations", json={"prompt": "x", "size": "big"}
    ).status_code == 422
    # n 越界
    assert client.post(
        "/api/v1/generations", json={"prompt": "x", "n": 0}
    ).status_code == 422
    assert client.post(
        "/api/v1/generations", json={"prompt": "x", "n": 11}
    ).status_code == 422
    # 未知 provider（取值域外的字面量）
    assert client.post(
        "/api/v1/generations", json={"prompt": "x", "provider": "midjourney"}
    ).status_code == 422
    client.close()
